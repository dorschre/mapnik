#!/usr/bin/env python3
"""Render a list of places at several zoom levels and derive their RDF.

    python3 render_places.py places.json out/ --zooms 19 18 17 16 15 14 13

For each place and zoom it writes <out>/<name>/<name>-z<zoom>.{png,json,ttl}:
the map image, the visual ground truth extracted from that same render, and
the CartoGraph RDF graph built from it.

The extent for a zoom is the one a slippy-map viewer would show at that zoom
for the given tile size, centred on the place, so the levels nest exactly.

Identity coverage is checked on every render: an object that cannot be linked
back to OSM is reported, per layer, and summarised at the end.
"""

import argparse
import json
import math
import os
import subprocess
import sys
import time

WEB_MERCATOR_RADIUS = 6378137.0
TILE_SIZE = 256


def extent_for(lat, lon, zoom, size):
    """Web Mercator extent of a `size` px view centred on lat/lon at `zoom`."""
    resolution = 2 * math.pi * WEB_MERCATOR_RADIUS / TILE_SIZE / (2 ** zoom)
    half = resolution * size / 2.0
    x = math.radians(lon) * WEB_MERCATOR_RADIUS
    y = math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0)) * WEB_MERCATOR_RADIUS
    return x - half, y - half, x + half, y + half


def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("places", help="JSON list of {name, label, country, lat, lon}")
    parser.add_argument("out", help="output directory")
    parser.add_argument("--zooms", nargs="+", type=int, default=[19, 18, 17, 16, 15, 14, 13])
    parser.add_argument("--size", type=int, default=512)
    parser.add_argument("--style", required=True, help="the compiled osm-carto Mapnik XML")
    parser.add_argument("--fonts", required=True)
    parser.add_argument("--plugins", default="build/out/plugins/input")
    parser.add_argument("--renderer", default="./build/out/mapnik-ground-truth-render")
    parser.add_argument("--to-rdf", default="demo/ground_truth/rdf/ground_truth_to_rdf.py")
    parser.add_argument("--python", default=sys.executable, help="interpreter that has rdflib")
    parser.add_argument("--base", default="https://example.org/maptrace/")
    parser.add_argument("--simplify", type=float, default=1.0)
    parser.add_argument("--only", nargs="+", help="only these place names")
    parser.add_argument("--skip-rdf", action="store_true")
    opts = parser.parse_args()

    places = json.load(open(opts.places))
    if opts.only:
        wanted = set(opts.only)
        places = [p for p in places if p["name"] in wanted]

    os.makedirs(opts.out, exist_ok=True)
    summary, started = [], time.time()

    for index, place in enumerate(places, 1):
        directory = os.path.join(opts.out, place["name"])
        os.makedirs(directory, exist_ok=True)
        for zoom in opts.zooms:
            prefix = os.path.join(directory, "{}-z{}".format(place["name"], zoom))
            extent = extent_for(place["lat"], place["lon"], zoom, opts.size)
            code, out, err = run([
                opts.renderer, opts.style, prefix,
                "--size", str(opts.size), str(opts.size),
                "--extent", *["{:.4f}".format(v) for v in extent],
                "--plugins", opts.plugins, "--fonts", opts.fonts,
                "--identity", "osm_id", "--simplify", str(opts.simplify),
            ])
            if code != 0:
                print("  !! {} z{} render failed: {}".format(place["name"], zoom, (err or out).strip()[:160]))
                summary.append(dict(place=place["name"], zoom=zoom, ok=False))
                continue

            stats = {}
            for line in out.splitlines():
                parts = line.split()
                if line.strip().startswith("objects"):
                    stats["objects"] = int(parts[1])
                elif line.strip().startswith("identity"):
                    stats["identified"] = int(parts[1])
                elif line.strip().startswith("elements"):
                    stats["displayed"] = int(parts[1])

            if not opts.skip_rdf:
                code, _, err = run([opts.python, opts.to_rdf, prefix + ".json",
                                    "-o", prefix + ".ttl",
                                    "--base", "{}{}/z{}/".format(opts.base, place["name"], zoom),
                                    "--infer-cased-lines"])
                if code != 0:
                    print("  !! {} z{} rdf failed: {}".format(place["name"], zoom, err.strip()[:160]))

            missing = stats.get("objects", 0) - stats.get("identified", 0)
            summary.append(dict(place=place["name"], zoom=zoom, ok=True, missing=missing, **stats))
            print("[{:3}/{}] {:16} z{:<2} objects={:<6} without osm_id={}".format(
                index, len(places), place["name"], zoom, stats.get("objects", "?"), missing))

    elapsed = time.time() - started
    ok = [s for s in summary if s["ok"]]
    incomplete = [s for s in ok if s.get("missing")]
    print("\n{} renders in {:.0f} min; {} failed".format(len(ok), elapsed / 60, len(summary) - len(ok)))
    print("total visible objects: {}".format(sum(s.get("objects", 0) for s in ok)))
    if incomplete:
        print("renders with objects that cannot be linked to OSM: {}".format(len(incomplete)))
        for s in incomplete[:10]:
            print("   {} z{}: {} of {}".format(s["place"], s["zoom"], s["missing"], s["objects"]))
    with open(os.path.join(opts.out, "summary.json"), "w") as handle:
        json.dump(summary, handle, indent=1)


if __name__ == "__main__":
    main()
