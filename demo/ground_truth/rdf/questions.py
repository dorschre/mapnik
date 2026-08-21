#!/usr/bin/env python3
"""Answer benchmark-style questions from a CartoGraph ground-truth graph.

    python3 questions.py dataset/Wien/Wien-z17.ttl

Every answer here is derived only from what the renderer said is visible, so it
is answerable by looking at the map image - which is the point. The questions
are grouped by the topological dimension of the display elements they rely on,
since that is what decides which spatial relation even makes sense:

    0D point anchors   icons, dots, point labels     -> counting, nearest, inside
    1D line elements   roads, rails, line labels     -> crossing, along, length
    2D area elements   buildings, landuse, water     -> coverage, containment

The interesting questions are the cross-dimensional ones: a 0D symbol inside a
2D area, a 1D road crossing a 2D park, two 1D roads meeting. Those need the
visible pixel geometry, not just bounding boxes.
"""

import argparse
import collections
import sys

try:
    from rdflib import Graph, Namespace
    from shapely import wkt as shapely_wkt
    from shapely.geometry import Point
    from shapely.ops import unary_union
except ImportError:
    sys.exit("this tool needs rdflib and shapely: pip install rdflib shapely")

CG = Namespace("https://w3id.org/cartograph#")

PREFIXES = """
PREFIX cg: <https://w3id.org/cartograph#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
"""


def load(path, ontology_dir):
    graph = Graph().parse(path)
    for name in ("cartograph-provenance.ttl", "cartograph.ttl"):
        graph.parse("{}/{}".format(ontology_dir, name), format="turtle")
    return graph


def data_ns(graph):
    """The namespace this graph minted its individuals in (bound as `ex`)."""
    for prefix, uri in graph.namespaces():
        if prefix == "ex":
            return str(uri)
    return ""


def ask(graph, query):
    return list(graph.query(PREFIXES + query))


def geometries(graph):
    """Visible outline of every object and element, as shapely geometry."""
    objects, elements = {}, {}
    for subject, _, literal in graph.triples((None, CG.pixelGeometry, None)):
        try:
            shape = shapely_wkt.loads(str(literal))
        except Exception:
            continue
        target = objects if "visible-object" in str(subject) else elements
        target[subject] = shape
    return objects, elements


def heading(text):
    print("\n" + text)
    print("-" * len(text))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("graph")
    parser.add_argument("--ontology", default="map-display-ontology")
    parser.add_argument("--limit", type=int, default=5)
    opts = parser.parse_args()

    graph = load(opts.graph, opts.ontology)
    limit = opts.limit

    # ---------------------------------------------------------------- shape
    heading("What is on this map, by dimension?")
    for row in ask(graph, """
        SELECT ?anchor (COUNT(?e) AS ?elements) (SUM(?px) AS ?pixels) WHERE {
          ?e a/rdfs:subClassOf* cg:DisplayElement ;
             cg:hasAnchorDimension ?c ; cg:pixelCount ?px .
          ?c skos:prefLabel ?anchor .
        } GROUP BY ?anchor ORDER BY ?anchor"""):
        print("  {:18} {:>5} elements, {:>8} px".format(str(row[0]), int(row[1]), int(row[2])))

    # ------------------------------------------------------------------- 0D
    heading("0D - questions about symbols and point labels")
    print("Q: which symbols are shown, and how many of each?")
    for row in ask(graph, """
        SELECT ?icon (COUNT(DISTINCT ?o) AS ?n) WHERE {
          ?e cg:hasPortrayal [ a cg:MarkerPortrayal ; cg:iconName ?icon ] ;
             cg:partOfVisibleObject ?o .
        } GROUP BY ?icon ORDER BY DESC(?n)""")[:limit]:
        print("     {:24} {}".format(str(row[0]), int(row[1])))

    print("Q: what kinds of point label are readable, and how many of each?")
    for row in ask(graph, """
        SELECT ?layer (COUNT(DISTINCT ?o) AS ?n) (SAMPLE(?label) AS ?example) WHERE {
          ?o cg:visibleLabel ?label ; cg:hasDisplayedElement ?e ; cg:inLayer [ cg:layerName ?layer ] .
          ?e cg:anchorDimension 0 .
        } GROUP BY ?layer ORDER BY DESC(?n)""")[:limit]:
        print("     {:22} {:>4}   e.g. {}".format(str(row[0]), int(row[1]), str(row[2])))

    print("Q: which named symbol sits closest to the centre of the image?")
    rows = ask(graph, """
        SELECT ?label ?x ?y ?w ?h WHERE {
          ?m a cg:MapImage ; cg:canvasWidthPx ?w ; cg:canvasHeightPx ?h .
          ?o cg:visibleLabel ?label ; cg:hasDisplayedElement ?e .
          ?e cg:pixelAnchorX ?x ; cg:pixelAnchorY ?y .
        }""")
    if rows:
        cx, cy = float(rows[0][3]) / 2, float(rows[0][4]) / 2
        best = min(rows, key=lambda r: (float(r[1]) - cx) ** 2 + (float(r[2]) - cy) ** 2)
        print("     {} at ({:.0f}, {:.0f})".format(str(best[0]), float(best[1]), float(best[2])))

    # ------------------------------------------------------------------- 1D
    heading("1D - questions about lines")
    print("Q: which street names are readable, and how much of each is drawn?")
    for row in ask(graph, """
        SELECT ?label (SUM(?px) AS ?pixels) WHERE {
          ?o cg:visibleLabel ?label ; cg:hasDisplayedElement ?e .
          ?e cg:anchorDimension 1 ; cg:pixelCount ?px .
        } GROUP BY ?label ORDER BY DESC(?pixels)""")[:limit]:
        print("     {:34} {:>6} px".format(str(row[0]), int(row[1])))

    print("Q: how is each road class drawn (colour and width)?")
    for row in ask(graph, """
        SELECT ?colour ?width (COUNT(?e) AS ?n) WHERE {
          ?e cg:anchorDimension 1 ; cg:inLayer [ cg:layerName ?layer ] ;
             cg:hasPortrayal [ a cg:StrokePortrayal ; cg:strokeColor ?colour ; cg:strokeWidthPx ?width ] .
          FILTER(STRSTARTS(?layer, "roads"))
        } GROUP BY ?colour ?width ORDER BY DESC(?n)""")[:limit]:
        print("     {:9} {:>6} px wide   x{}".format(str(row[0]), str(row[1]), int(row[2])))

    print("Q: which visible roads actually meet on the image?")
    # A road's name and a road's body are different visible objects here:
    # openstreetmap-carto draws them from separate stylesheet layers
    # (roads-text-name vs roads-casing / roads-fill). Only the shared osm_id
    # ties them together, which is exactly what identity metadata is for.
    named = ask(graph, """
        SELECT ?id ?label WHERE {
          ?o cg:visibleLabel ?label ; cg:representsFeature ?f ; cg:inLayer [ cg:layerName ?layer ] .
          ?f <%sosm_id> ?id .
          FILTER(STRSTARTS(?layer, "roads"))
        }""" % data_ns(graph))
    names = {str(i): str(l) for i, l in named}
    parts = collections.defaultdict(list)
    for row in ask(graph, """
        SELECT ?id ?geom WHERE {
          ?o cg:pixelGeometry ?geom ; cg:representsFeature ?f ; cg:hasDisplayedElement ?e ;
             cg:inLayer [ cg:layerName ?layer ] .
          ?e cg:anchorDimension 1 .
          ?f <%sosm_id> ?id .
          FILTER(STRSTARTS(?layer, "roads"))
        }""" % data_ns(graph)):
        if str(row[0]) in names:
            try:
                parts[str(row[0])].append(shapely_wkt.loads(str(row[1])))
            except Exception:
                pass
    merged = {names[i]: unary_union(v).buffer(1.0) for i, v in parts.items()}
    crossings, keys = [], sorted(merged)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if a != b and merged[a].intersects(merged[b]):
                crossings.append((a, b))
    for a, b in crossings[:limit]:
        print("     {} x {}".format(a, b))
    print("     ({} crossing pairs among {} named streets)".format(len(crossings), len(keys)))

    # ------------------------------------------------------------------- 2D
    heading("2D - questions about areas")
    print("Q: how much of the image does each kind of surface cover?")
    total = ask(graph, "SELECT ?w ?h WHERE { ?m a cg:MapImage ; cg:canvasWidthPx ?w ; cg:canvasHeightPx ?h }")
    canvas = int(total[0][0]) * int(total[0][1]) if total else 1
    for row in ask(graph, """
        SELECT ?layer (SUM(?px) AS ?pixels) WHERE {
          ?e cg:anchorDimension 2 ; cg:pixelCount ?px ; cg:inLayer [ cg:layerName ?layer ] .
        } GROUP BY ?layer ORDER BY DESC(?pixels)""")[:limit]:
        print("     {:22} {:>7} px  {:5.1f}% of the image".format(
            str(row[0]), int(row[1]), 100.0 * int(row[1]) / canvas))

    print("Q: which areas are cut off by the edge of the image?")
    for row in ask(graph, """
        SELECT ?layer (COUNT(DISTINCT ?o) AS ?n) WHERE {
          ?e cg:anchorDimension 2 ; cg:isClipped true ; cg:partOfVisibleObject ?o ;
             cg:inLayer [ cg:layerName ?layer ] .
        } GROUP BY ?layer ORDER BY DESC(?n)""")[:limit]:
        print("     {:22} {}".format(str(row[0]), int(row[1])))

    # --------------------------------------------------------- cross-dimension
    heading("Across dimensions - the questions bounding boxes cannot answer")
    objects, _ = geometries(graph)
    readable = {s: str(o) for s, _, o in graph.triples((None, CG.visibleLabel, None))}
    labels = dict(readable)
    # An unlabelled area is still a perfectly good answer target; name it by
    # the layer it came from rather than by a bare number.
    for row in ask(graph, """
        SELECT ?o ?layer ?id WHERE {
          ?o a cg:VisibleObject ; cg:inLayer [ cg:layerName ?layer ] ; cg:featureId ?id .
        }"""):
        if row[0] not in labels or labels[row[0]].strip().isdigit():
            labels[row[0]] = "{} #{}".format(str(row[1]), str(row[2]))
    dims = {}
    for subject in objects:
        rows = ask(graph, """
            SELECT ?d WHERE {{ <{}> cg:hasDisplayedElement ?e . ?e cg:anchorDimension ?d }}""".format(subject))
        dims[subject] = {int(r[0]) for r in rows}

    areas = [s for s in objects if 2 in dims.get(s, ()) and objects[s].area > 2000]
    points = [s for s in objects if 0 in dims.get(s, ())]
    print("Q: which symbols stand inside a large visible area?")
    shown = 0
    for area in sorted(areas, key=lambda s: -objects[s].area)[:3]:
        inside = [labels.get(p, str(p).rsplit("/", 1)[-1]) for p in points
                  if objects[area].intersects(objects[p])]
        name = labels.get(area, str(area).rsplit("/", 1)[-1])
        print("     {:28} ({:.0f} px) contains {} symbol(s): {}".format(
            name, objects[area].area, len(inside), ", ".join(inside[:4]) or "-"))
        shown += 1
    if not shown:
        print("     (no large visible areas in this view)")

    print("Q: which named streets run through the largest visible area?")
    if areas:
        biggest = max(areas, key=lambda s: objects[s].area)
        crossing = [readable[s] for s in objects
                    if s in readable and 1 in dims.get(s, ()) and objects[s].intersects(objects[biggest])]
        print("     {} is crossed by: {}".format(
            labels.get(biggest, "the largest area"), ", ".join(sorted(set(crossing))[:6]) or "-"))


if __name__ == "__main__":
    main()
