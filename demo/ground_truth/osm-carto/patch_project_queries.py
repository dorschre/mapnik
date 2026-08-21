"""Give openstreetmap-carto's datasource queries an osm_id to project.

Only the id: osm_type and the wikidata tag are added afterwards by
enrich_identity.py, which looks them up in the database. Projecting all three
here cannot be done consistently - a level that already carries osm_id would
have to be extended with the other two as well, and eight layers stop loading
when that goes wrong.

Upstream selects no identifier at all, so every visible object extracted from a
render is anonymous. This adds `osm_id` next to `way` at each SELECT level that
does not already project one - each level matters, because an enclosing SELECT
can only pass the column up once the subquery below supplies it.

Not every query can take one:
  * the external-data tables (coastlines, ice sheets) have no osm_id at all;
  * an aggregate (GROUP BY) cannot project a free column;
  * some UNION branches project a computed column instead of a bare `way`, so
    patching the others would leave the branches with different widths.
The first two are refused here; the third is left to PostgreSQL, which rejects
the query at load time - re-run with that layer's id passed as an argument to
skip it.
"""

import re
import sys

NO_IDENTITY = ("water_polygons", "simplified_water_polygons",
               "icesheet_outlines", "icesheet_polygons",
               "ne_110m_admin_0_boundary_lines_land")

# Anchored on SELECT itself, not on the first column: branches routinely
# project a computed geometry (ST_PointOnSurface(way) AS way) or start with
# some other column entirely, and every branch of a UNION has to be treated the
# same way or the widths stop matching.
SELECT_HEAD = re.compile(r'\bSELECT\s+(?:DISTINCT\s+ON\s*\([^)]*\)\s+)?')
PROJECTS_OSM_ID = re.compile(r'^\s*(?:\w+\.)?osm_id\s*,?\s*$', re.M)


def is_table_subquery(sql, select_start):
    """True when this SELECT stands where a table may stand.

    A SELECT sitting in a column or IN(...) position is a scalar subquery and
    must return exactly one column, so it can never take an extra one. A SELECT
    in table position follows FROM, JOIN or a set operator, or opens the block.

    A trailing comma is deliberately not accepted: it far more often ends a
    column in a select list than separates two tables in a FROM.
    """
    head = sql[:select_start].rstrip()
    if head.endswith("("):
        head = head[:-1].rstrip()
    if not head:
        return True
    return bool(re.search(r'\b(?:FROM|JOIN|UNION|UNION\s+ALL|EXCEPT|INTERSECT)$', head, re.I))


def projection_list(sql, start):
    """The select list of this SELECT: everything up to its own FROM.

    Parenthesis depth has to be tracked, because a projected column may itself
    contain a FROM - CASE ... EXISTS (SELECT 1 FROM carto_pois ...) is common in
    openstreetmap-carto - and stopping at that one hides the rest of the list.
    """
    depth = 0
    for token in re.finditer(r'\(|\)|\bFROM\b', sql[start:]):
        text = token.group(0)
        if text == "(":
            depth += 1
        elif text == ")":
            if depth == 0:
                return sql[start:start + token.start()]
            depth -= 1
        elif depth == 0:
            return sql[start:start + token.start()]
    return sql[start:]


def split_columns(projection):
    """Top-level column items of a select list."""
    items, depth, current = [], 0, []
    for ch in projection:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            items.append("".join(current)); current = []
        else:
            current.append(ch)
    items.append("".join(current))
    return [i.strip() for i in items if i.strip()]


def already_projects_osm_id(projection):
    """True only when osm_id is an output column, not merely used in one.

    capital-names computes `round(ascii(md5(osm_id::text)) / 55) AS dir`; that
    mentions osm_id without projecting it, and treating it as identity would
    leave the layer anonymous.
    """
    for item in split_columns(projection):
        alias = re.search(r'\bAS\s+(\w+)\s*$', item, re.I)
        name = alias.group(1) if alias else item
        if re.fullmatch(r'(?:\w+\.)?osm_id', name.strip()):
            return True
    return False


def qualifier(sql, start):
    """Table alias to qualify osm_id with, when several relations are in scope.

    turning-circle joins planet_osm_point p with planet_osm_line l; both carry
    osm_id, so a bare one is ambiguous and the first relation's alias is used.
    Only this SELECT's own FROM counts - a nested subquery's tables are not in
    scope here, and borrowing an alias from one produces "missing FROM-clause
    entry".
    """
    clause, depth = [], 0
    for token in re.finditer(r'\(|\)|[^()]+', sql[start:]):
        text = token.group(0)
        if text == "(":
            depth += 1
        elif text == ")":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0:
            stop = re.search(r'\b(WHERE|GROUP\s+BY|ORDER\s+BY|UNION|LIMIT)\b', text, re.I)
            if stop:
                clause.append(text[: stop.start()])
                break
            clause.append(text)
    clause = "".join(clause)
    aliases = re.findall(r'\b(planet_osm_\w+)\s+(?:AS\s+)?(\w+)\b', clause, re.I)
    tables = re.findall(r'\bplanet_osm_\w+', clause, re.I)
    if len(tables) > 1 and aliases:
        return aliases[0][1] + "."
    return ""


def add_osm_id(sql):
    def patch(match):
        if not is_table_subquery(sql, match.start()):
            return match.group(0)
        span = projection_list(sql, match.end())
        # `SELECT *` already carries whatever the subquery below provides;
        # adding osm_id beside it would name the same column twice.
        if span.lstrip().startswith("*"):
            return match.group(0)
        if already_projects_osm_id(span):
            return match.group(0)
        from_at = sql.find(span, match.end()) + len(span)
        return match.group(0) + qualifier(sql, from_at) + "osm_id, "

    return SELECT_HEAD.sub(patch, sql)


def blocks(lines):
    i = 0
    while i < len(lines):
        # The block may carry a YAML anchor (`table: &roads_sql |-`, or with a
        # hyphen: `&turning-circle_sql`); that one query is then aliased by
        # several layers, so patching it once fixes all of them.
        m = re.match(r'^(\s+)table: (?:&[\w.-]+\s+)?\|-\s*$', lines[i])
        if m:
            indent = len(m.group(1))
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or
                                      len(lines[j]) - len(lines[j].lstrip()) > indent):
                j += 1
            yield i + 1, j
            i = j
        else:
            i += 1


def patch_file(path, skip):
    lines = open(path).read().splitlines(keepends=True)
    out, patched = list(lines), []
    for start, end in reversed(list(blocks(lines))):
        sql = "".join(lines[start:end])
        name = re.search(r'\)\s*AS\s+(\w+)\s*$', sql.strip())
        name = name.group(1) if name else "?"
        if name in skip or any(t in sql for t in NO_IDENTITY) or "GROUP BY" in sql.upper():
            continue
        new = add_osm_id(sql)
        if new != sql:
            out[start:end] = [new]
            patched.append(name)
    open(path, "w").write("".join(out))
    return patched


if __name__ == "__main__":
    done = patch_file(sys.argv[1], set(sys.argv[2:]))
    print("added osm_id to {} datasource queries".format(len(done)))
