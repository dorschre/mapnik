#!/usr/bin/env python3
"""Turn visual ground truth into detection / instance-segmentation labels.

    python3 export_annotations.py dataset/ out/ --format coco yolo normalized

Reads the `*.json` ground truth written by mapnik-ground-truth-render and emits
training labels whose polygons are the *visible* outline of each object - the
part that actually survived to the image - in relative coordinates.

Formats
  coco        one instances.json (COCO detection + segmentation)
  yolo        one <image>.txt per image, YOLO segmentation layout
  normalized  one <image>.json per image: bbox, polygons and anchor, all in
              0..1, plus the object's identity and any visible label

Instances
  --level object   (default) one instance per source feature: a road broken by
                   junctions and labels stays one instance with several parts
  --level element  one instance per graphical occurrence: casing and fill of
                   the same road become separate instances

Dimensions are all present and all useful:
  2D buildings, water, landcover  -> polygon masks
  1D roads, railways, waterways   -> the drawn *ribbon*, not a centreline: what
                                     a segmentation model can actually see
  0D icons and dots               -> a small polygon plus a point anchor, so the
                                     same export serves detection and keypoints

Coordinates are relative to the image: x / width, y / height, origin top-left,
y downwards - the convention COCO and YOLO already use.

Source
  --source json  (default) read the ground-truth JSON. Fast.
  --source rdf   read the Turtle graph instead, via SPARQL. Slower, but proves
                 the RDF carries everything the labels need; `--check` runs both
                 and compares them.
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict


OBJECT_QUERY = """
PREFIX cg: <https://w3id.org/cartograph#>
SELECT ?object ?layer ?featureId ?pixels ?geometry ?label WHERE {
  ?object a cg:VisibleObject ;
          cg:inLayer [ cg:layerName ?layer ] ;
          cg:featureId ?featureId ;
          cg:visiblePixelCount ?pixels ;
          cg:pixelGeometry ?geometry .
  OPTIONAL { ?object cg:visibleLabel ?label }
}
"""

ELEMENT_QUERY = """
PREFIX cg: <https://w3id.org/cartograph#>
SELECT ?element ?object ?dimension ?pixels ?layer ?featureId ?geometry ?text WHERE {
  ?element cg:partOfVisibleObject ?object ;
           cg:anchorDimension ?dimension ;
           cg:pixelCount ?pixels ;
           cg:inLayer [ cg:layerName ?layer ] ;
           cg:depictsFeature [ cg:featureId ?featureId ] ;
           cg:pixelGeometry ?geometry .
  OPTIONAL { ?element cg:hasPortrayal [ cg:renderedText ?text ] }
}
"""

CANVAS_QUERY = """
PREFIX cg: <https://w3id.org/cartograph#>
SELECT ?width ?height WHERE {
  ?image a cg:MapImage ; cg:canvasWidthPx ?width ; cg:canvasHeightPx ?height .
}
"""


def rings_from_wkt(wkt):
    """MULTIPOLYGON (((x y, ...), (hole)), ...) -> [[ [x,y], ... ], ...] per ring.

    A tiny reader rather than a geometry library: the exporter only ever needs
    the coordinates back, and this keeps the RDF path dependency-free.
    """
    inner = wkt[wkt.index("(") + 1: wkt.rindex(")")]
    parts, depth, current = [], 0, []
    for ch in inner:
        if ch == "(":
            depth += 1
            if depth == 1:
                current = []
                continue
        elif ch == ")":
            depth -= 1
            if depth == 0:
                parts.append("".join(current))
                continue
        if depth >= 1:
            current.append(ch)
    out = []
    for part in parts:
        rings = []
        for ring in part.split("),"):
            ring = ring.strip().strip("()")
            points = [[float(v) for v in pair.split()] for pair in ring.split(",") if pair.strip()]
            if points:
                rings.append(points)
        if rings:
            out.append(rings)
    return out


def truth_from_graph(path):
    """Rebuild the exporter's input from the RDF graph, using SPARQL only."""
    from rdflib import Graph

    graph = Graph().parse(path)
    canvas = list(graph.query(CANVAS_QUERY))
    width, height = (int(canvas[0][0]), int(canvas[0][1])) if canvas else (0, 0)

    # Real display elements, so --level element works from the graph too.
    elements, by_object, dominant = [], {}, {}
    for row in graph.query(ELEMENT_QUERY):
        element_uri, object_uri, dimension, pixels, layer, feature_id, geometry, text = row
        element_id = len(elements) + 1
        elements.append(dict(
            id=element_id, sequence=element_id, type=str(layer),
            layer=str(layer), feature_id=str(feature_id),
            anchor_dimension=int(dimension), pixel_count=int(pixels),
            visibility_status="rendered_complete",
            rendered_text=(str(text) if text else None),
            pixel_geometry=dict(type="MultiPolygon", coordinates=rings_from_wkt(str(geometry)))))
        by_object.setdefault(str(object_uri), []).append(element_id)
        best = dominant.get(str(object_uri))
        if best is None or int(pixels) > best[1]:
            dominant[str(object_uri)] = (int(dimension), int(pixels))

    objects = []
    for object_uri, layer, feature_id, pixels, geometry, label in graph.query(OBJECT_QUERY):
        key = str(object_uri)
        # Keep the dominant element first: the exporter reads the object's
        # dimension from the element that owns the most visible pixels.
        own = sorted(by_object.get(key, []),
                     key=lambda i: -elements[i - 1]["pixel_count"])
        objects.append(dict(
            identity=dict(layer=str(layer), feature_id=str(feature_id)),
            visible_properties=({"label": str(label)} if label else {}),
            displayed_elements=own,
            visible_pixel_count=int(pixels),
            visible_geometry=dict(type="MultiPolygon", coordinates=rings_from_wkt(str(geometry)))))

    return dict(map=dict(width=width, height=height), elements=elements, objects=objects)


def load_classes(path):
    spec = json.load(open(path))["classes"]
    # longest prefix wins, so "building-text" is not caught by "buildings"
    return sorted(((k, v) for k, v in spec.items()), key=lambda kv: -len(kv[0]))


def class_of(layer, rules):
    for prefix, name in rules:
        if layer == prefix or layer.startswith(prefix):
            return name
    return None


def rings_of(geometry):
    """(exterior, holes) per part; holes are kept separately, see below."""
    if not geometry:
        return []
    return [(part[0], part[1:]) for part in geometry["coordinates"]]


def flatten(ring, width, height, relative):
    out = []
    for x, y in ring:
        out.extend((x / width, y / height) if relative else (x, y))
    return out


def instances(truth, rules, level, min_area, min_pixels, dimension_rule="max"):
    """Yield one annotation dict per instance."""
    elements = {e["id"]: e for e in truth.get("elements", [])}
    if level == "element":
        source = [(e, e.get("pixel_geometry"), e.get("pixel_count", 0), e["layer"],
                   {"feature_id": e["feature_id"], "layer": e["layer"]},
                   e.get("rendered_text"), e.get("anchor_dimension", 2)) for e in truth.get("elements", [])
                  if e.get("visibility_status", "").startswith("rendered")]
    else:
        source = []
        for obj in truth.get("objects", []):
            own = [elements[i] for i in obj.get("displayed_elements", []) if i in elements]
            # An object usually has marks of several dimensions: openstreetmap-carto
            # draws a building as a 2D fill *and* a 1D outline stroke, a landcover
            # patch likewise, a labelled POI as a 0D icon plus 0D text.
            #
            #   max       the object is as high-dimensional as its highest mark,
            #             so a building is an area even when its outline happens
            #             to own more surviving pixels than its interior
            #   dominant  the mark owning the most visible pixels wins, which
            #             follows what the object looks like right now
            dimensions = [e.get("anchor_dimension", 2) for e in own] or [2]
            if dimension_rule == "dominant":
                dominant = max(own, key=lambda e: e.get("pixel_count", 0), default=None)
                dimension = dominant.get("anchor_dimension", 2) if dominant else 2
            else:
                dimension = max(dimensions)
            source.append((obj, obj.get("visible_geometry"), obj.get("visible_pixel_count", 0),
                           obj["identity"].get("layer", ""), obj["identity"],
                           obj.get("visible_properties", {}).get("label"), dimension))

    for record, geometry, pixels, layer, identity, label, dimension in source:
        category = class_of(layer, rules)
        if category is None or not geometry or pixels < min_pixels:
            continue
        parts = [(ext, holes) for ext, holes in rings_of(geometry) if len(ext) >= 4]
        if not parts:
            continue
        xs = [p[0] for ext, _ in parts for p in ext]
        ys = [p[1] for ext, _ in parts for p in ext]
        if (max(xs) - min(xs)) * (max(ys) - min(ys)) < min_area:
            continue
        yield dict(category=category, layer=layer, identity=identity, label=label,
                   dimension=dimension, pixels=pixels, parts=parts,
                   bbox=(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)))


def anchor(instance):
    """Representative point: the centre of the bounding box of the biggest part."""
    ext = max((p[0] for p in instance["parts"]), key=len)
    xs = [p[0] for p in ext]
    ys = [p[1] for p in ext]
    return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="a ground-truth .json, or a directory of them")
    parser.add_argument("out")
    parser.add_argument("--format", nargs="+", default=["coco"], choices=["coco", "yolo", "normalized"])
    parser.add_argument("--level", choices=["object", "element"], default="object")
    parser.add_argument("--classes", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "classes.json"))
    parser.add_argument("--min-pixels", type=int, default=12, help="drop instances with fewer visible pixels")
    parser.add_argument("--min-area", type=float, default=4.0, help="drop instances whose bbox is smaller, in px^2")
    parser.add_argument("--absolute", action="store_true", help="write pixel coordinates instead of 0..1")
    parser.add_argument("--dimension-rule", choices=["max", "dominant"], default="max",
                        help="an object's dimension: its highest-dimensional mark (default), "
                             "or the mark owning the most visible pixels")
    parser.add_argument("--match", help="only ground-truth files whose name contains this, e.g. -z17")
    parser.add_argument("--source", choices=["json", "rdf"], default="json",
                        help="derive the labels from the ground-truth JSON, or from the Turtle graph via SPARQL")
    opts = parser.parse_args()

    if os.path.isdir(opts.input):
        files = sorted(os.path.join(root, name)
                       for root, _, names in os.walk(opts.input)
                       for name in names
                       if name.endswith(".ttl" if opts.source == "rdf" else ".json")
                       and not name.endswith(".norm.json")
                       and name not in ("summary.json", "manifest.json")
                       and (not opts.match or opts.match in name))
    else:
        files = [opts.input]

    rules = load_classes(opts.classes)
    os.makedirs(opts.out, exist_ok=True)
    relative = not opts.absolute

    categories, coco_images, coco_annotations = {}, [], []
    counts, skipped = Counter(), 0

    for image_id, path in enumerate(files, 1):
        truth = truth_from_graph(path) if opts.source == "rdf" else json.load(open(path))
        if "elements" not in truth:
            skipped += 1
            continue
        width, height = truth["map"]["width"], truth["map"]["height"]
        stem = os.path.splitext(os.path.basename(path))[0]
        coco_images.append(dict(id=image_id, file_name=stem + ".png", width=width, height=height))

        yolo_lines, normalized = [], []
        for instance in instances(truth, rules, opts.level, opts.min_area, opts.min_pixels,
                                  opts.dimension_rule):
            name = instance["category"]
            categories.setdefault(name, len(categories) + 1)
            counts[name] += 1

            x, y, w, h = instance["bbox"]
            segmentation = [flatten(ext, width, height, False) for ext, _ in instance["parts"]]
            coco_annotations.append(dict(
                id=len(coco_annotations) + 1, image_id=image_id, category_id=categories[name],
                bbox=[x, y, w, h], area=float(instance["pixels"]), iscrowd=0,
                segmentation=segmentation,
                attributes=dict(layer=instance["layer"], dimension=instance["dimension"],
                                label=instance["label"], identity=instance["identity"])))

            if "yolo" in opts.format:
                for ext, _ in instance["parts"]:
                    coords = flatten(ext, width, height, True)
                    yolo_lines.append("{} {}".format(
                        categories[name] - 1, " ".join("{:.6f}".format(v) for v in coords)))

            if "normalized" in opts.format:
                ax, ay = anchor(instance)
                normalized.append(dict(
                    category=name, layer=instance["layer"], dimension=instance["dimension"],
                    label=instance["label"], identity=instance["identity"],
                    visible_pixels=instance["pixels"],
                    bbox=[x / width, y / height, w / width, h / height],
                    anchor=[ax / width, ay / height],
                    polygons=[flatten(ext, width, height, relative) for ext, _ in instance["parts"]],
                    holes=[flatten(ring, width, height, relative)
                           for _, holes in instance["parts"] for ring in holes]))

        if "yolo" in opts.format:
            with open(os.path.join(opts.out, stem + ".txt"), "w") as handle:
                handle.write("\n".join(yolo_lines) + ("\n" if yolo_lines else ""))
        if "normalized" in opts.format:
            with open(os.path.join(opts.out, stem + ".norm.json"), "w") as handle:
                json.dump(dict(image=stem + ".png", width=width, height=height,
                               coordinates="relative" if relative else "pixels",
                               instances=normalized), handle)

    if "coco" in opts.format:
        with open(os.path.join(opts.out, "instances.json"), "w") as handle:
            json.dump(dict(
                info=dict(description="Visible-only instance segmentation from mapnik visual ground truth",
                          note="Polygons are the outline of the pixels that survived to the image. "
                               "Holes are dropped in COCO segmentation; see the .norm.json files for them."),
                images=coco_images,
                categories=[dict(id=i, name=n) for n, i in sorted(categories.items(), key=lambda kv: kv[1])],
                annotations=coco_annotations), handle)
    if "yolo" in opts.format:
        with open(os.path.join(opts.out, "classes.txt"), "w") as handle:
            handle.write("\n".join(n for n, _ in sorted(categories.items(), key=lambda kv: kv[1])) + "\n")

    print("{} image(s), {} instance(s){}".format(
        len(coco_images), len(coco_annotations), ", {} skipped".format(skipped) if skipped else ""))
    for name, n in counts.most_common():
        print("  {:12} {}".format(name, n))


if __name__ == "__main__":
    main()
