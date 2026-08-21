#!/usr/bin/env python3
"""Resolve each object's osm_id to a real OSM element, and to Wikidata.

    python3 enrich_identity.py dataset/Wien/Wien-z17.json
    python3 enrich_identity.py dataset/ --recurse

The renderer can only report the `osm_id` column the stylesheet selects. That
number alone does not identify an element: the same integer exists as a node,
as a way and as a relation. Which one it is depends on the osm2pgsql table the
row came from, and that is not in the ground truth - so it is looked up here:

    planet_osm_point    -> node
    planet_osm_line     -> way
    planet_osm_polygon  -> way when the id is positive, relation when negative
                           (osm2pgsql stores multipolygon relations negated)

The same lookup returns the element's `wikidata` tag, which is what actually
links a map object to an external entity.

Each object's identity gains:

    osm_type      node | way | relation
    osm_url       https://www.openstreetmap.org/<type>/<id>
    wikidata      Q… when the element carries the tag
    osm_ambiguous true when the id exists in more than one table, so the type
                  is a best guess rather than a fact

Identity metadata, not visible information: none of this is readable on the map.
Needs psql reachable, i.e. the compose database from setup.sh.
"""

import argparse
import json
import os
import subprocess
import sys

TABLES = (("planet_osm_point", "node"), ("planet_osm_line", "way"), ("planet_osm_polygon", "area"))


def lookup(ids, container, database, user, batch=20000):
    """osm_id -> (type, wikidata, ambiguous), read from the osm2pgsql tables."""
    found = {}
    for start in range(0, len(ids), batch):
        merge(found, lookup_batch(ids[start:start + batch], container, database, user))
    return found


def merge(found, batch):
    for osm_id, entry in batch.items():
        previous = found.get(osm_id)
        if previous is None:
            found[osm_id] = entry
        else:
            if previous[0] != entry[0]:
                previous[2] = True
            if entry[1] and not previous[1]:
                previous[1] = entry[1]


def lookup_batch(ids, container, database, user):
    if not ids:
        return {}
    values = ",".join("({})".format(int(i)) for i in ids)
    union = "\nUNION ALL\n".join(
        "SELECT t.osm_id, '{kind}' AS src, t.tags->'wikidata' AS wd "
        "FROM {table} t JOIN (VALUES {values}) AS w(id) ON t.osm_id = w.id".format(
            kind=kind, table=table, values=values)
        for table, kind in TABLES)
    sql = "SELECT osm_id, src, coalesce(wd,'') FROM (\n{}\n) q GROUP BY 1,2,3".format(union)

    # The query goes in on stdin: a city at z14 has tens of thousands of ids,
    # and passing that as an argument overruns the command line ("Argument list
    # too long").
    result = subprocess.run(
        ["docker", "exec", "-i", container, "psql", "-U", user, "-d", database, "-tAF", "\t", "-f", "-"],
        input=sql, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit("psql failed: {}".format(result.stderr.strip()[:300]))

    found = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        osm_id, source, wikidata = int(parts[0]), parts[1], parts[2]
        # An area id is a way when positive and a relation when negative.
        kind = source if source != "area" else ("way" if osm_id > 0 else "relation")
        previous = found.get(osm_id)
        if previous is None:
            found[osm_id] = [kind, wikidata, False]
        else:
            if previous[0] != kind:
                previous[2] = True          # the same number in two tables
            if wikidata and not previous[1]:
                previous[1] = wikidata
    return found


def enrich(path, container, database, user, dry_run):
    truth = json.load(open(path))
    objects = truth.get("objects", [])
    ids = {o["identity"]["osm_id"] for o in objects
           if isinstance(o.get("identity", {}).get("osm_id"), int)}
    found = lookup(sorted(ids), container, database, user)

    typed = linked = ambiguous = 0
    for obj in objects:
        osm_id = obj.get("identity", {}).get("osm_id")
        entry = found.get(osm_id) if isinstance(osm_id, int) else None
        if not entry:
            continue
        kind, wikidata, is_ambiguous = entry
        obj["identity"]["osm_type"] = kind
        obj["identity"]["osm_url"] = "https://www.openstreetmap.org/{}/{}".format(kind, abs(osm_id))
        typed += 1
        if wikidata:
            obj["identity"]["wikidata"] = wikidata
            linked += 1
        if is_ambiguous:
            obj["identity"]["osm_ambiguous"] = True
            ambiguous += 1

    if not dry_run:
        with open(path, "w") as handle:
            json.dump(truth, handle)
    print("{:44} {:>6} objects, {:>6} typed, {:>5} linked to wikidata{}".format(
        os.path.basename(path), len(objects), typed, linked,
        ", {} ambiguous".format(ambiguous) if ambiguous else ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", help="a ground-truth .json, or a directory")
    parser.add_argument("--recurse", action="store_true")
    parser.add_argument("--container", default="openstreetmap-carto-db-1")
    parser.add_argument("--database", default="gis")
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--dry-run", action="store_true", help="report without rewriting the files")
    opts = parser.parse_args()

    if os.path.isdir(opts.path):
        files = sorted(os.path.join(base, name)
                       for base, _, names in os.walk(opts.path)
                       for name in names
                       if name.endswith(".json") and not name.endswith(".norm.json")
                       and name not in ("summary.json", "manifest.json"))
        if not opts.recurse:
            files = [f for f in files if os.path.dirname(f) == opts.path]
    else:
        files = [opts.path]

    for path in files:
        enrich(path, opts.container, opts.database, opts.user, opts.dry_run)


if __name__ == "__main__":
    main()
