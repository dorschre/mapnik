#!/usr/bin/env python3
"""Fetch a small OpenStreetMap extract via Overpass and write it as GeoJSON.

    python3 fetch_osm.py --bbox 52.5135 13.3750 52.5225 13.3950 --out-dir .

Produces one GeoJSON file per thematic layer (landuse, water, buildings, roads,
places), which the mapnik geojson plugin can read directly - no database, no
osm2pgsql. Coordinates stay in WGS84; the stylesheet reprojects.

The OSM id and type are kept on every feature so the ground truth can carry
them as identity metadata.
"""

import argparse
import json
import os
import urllib.parse
import urllib.request

ENDPOINT = "https://overpass-api.de/api/interpreter"

QUERY = """
[out:json][timeout:120];
(
  way["highway"]({bbox});
  way["building"]({bbox});
  way["natural"~"water|wood|scrub"]({bbox});
  way["waterway"]({bbox});
  way["landuse"]({bbox});
  way["leisure"]({bbox});
  node["place"]({bbox});
  node["railway"="station"]({bbox});
  node["tourism"]({bbox});
);
out geom;
"""

# tag -> which layer an element belongs to, first match wins
LAYERS = [
    ("water", lambda t: t.get("natural") == "water" or "waterway" in t),
    ("landuse", lambda t: "landuse" in t or "leisure" in t or t.get("natural") in ("wood", "scrub")),
    ("buildings", lambda t: "building" in t),
    ("roads", lambda t: "highway" in t),
    ("places", lambda t: "place" in t or "railway" in t or "tourism" in t),
]

AREA_KEYS = ("building", "landuse", "leisure", "natural", "amenity")


def classify(tags):
    for name, predicate in LAYERS:
        if predicate(tags):
            return name
    return None


def is_area(element, tags):
    geometry = element.get("geometry") or []
    if len(geometry) < 4:
        return False
    if geometry[0] != geometry[-1]:
        return False
    if "highway" in tags and not any(k in tags for k in AREA_KEYS):
        return False
    return tags.get("area") != "no"


def to_feature(element):
    tags = element.get("tags", {})
    layer = classify(tags)
    if layer is None:
        return None, None

    properties = dict(tags)
    properties["osm_id"] = element["id"]
    properties["osm_type"] = element["type"]

    if element["type"] == "node":
        geometry = {"type": "Point", "coordinates": [element["lon"], element["lat"]]}
    else:
        coords = [[p["lon"], p["lat"]] for p in element.get("geometry", [])]
        if len(coords) < 2:
            return None, None
        if is_area(element, tags):
            geometry = {"type": "Polygon", "coordinates": [coords]}
        else:
            geometry = {"type": "LineString", "coordinates": coords}

    return layer, {"type": "Feature", "properties": properties, "geometry": geometry}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox", nargs=4, type=float, required=True,
                        metavar=("SOUTH", "WEST", "NORTH", "EAST"))
    parser.add_argument("--out-dir", default=".")
    parser.add_argument("--endpoint", default=ENDPOINT)
    opts = parser.parse_args()

    bbox = "{},{},{},{}".format(*opts.bbox)
    request = urllib.request.Request(
        opts.endpoint,
        data=urllib.parse.urlencode({"data": QUERY.format(bbox=bbox)}).encode(),
        headers={"User-Agent": "mapnik-visual-ground-truth-demo"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.load(response)

    collections = {name: [] for name, _ in LAYERS}
    for element in payload.get("elements", []):
        layer, feature = to_feature(element)
        if feature is not None:
            collections[layer].append(feature)

    os.makedirs(opts.out_dir, exist_ok=True)
    for name, features in collections.items():
        path = os.path.join(opts.out_dir, name + ".geojson")
        with open(path, "w") as handle:
            json.dump({"type": "FeatureCollection", "features": features}, handle)
        print("{:10} {:5} features -> {}".format(name, len(features), path))


if __name__ == "__main__":
    main()
