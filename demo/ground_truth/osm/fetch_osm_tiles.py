#!/usr/bin/env python3
"""Fetch the real OSM Standard raster for a bbox, as a reference image.

    python3 fetch_osm_tiles.py --bbox 13.3745 52.5145 13.3820 52.5180 --zoom 17 \
        -o reference.png

This downloads and stitches tiles from tile.openstreetmap.org. It is a
*reference picture only*: a rendered tile carries no element identity, no draw
order and no feature ids, so no visual ground truth can be derived from it. To
get ground truth for the same view you have to render it yourself - see
`--emit-extent`, which prints the exact Web Mercator extent and pixel size to
hand to mapnik-ground-truth-render.

Tiles are cached under ~/.cache/mapnik-ground-truth/osm-tiles and requests are
spaced out, per the OSM tile usage policy. Keep the area small, set a real
contact in --user-agent, and attribute "(c) OpenStreetMap contributors" wherever
the image is shown.

The slippy-map tiling arithmetic follows fetch-osm-bbox.py by Rene Dorsch.
"""

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("this tool needs Pillow: pip install Pillow")

TILE_SIZE = 256
MAX_MERCATOR_LATITUDE = 85.0511287798066
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
WEB_MERCATOR_RADIUS = 6378137.0


def lon_to_pixel(lon, zoom):
    return (lon + 180.0) / 360.0 * TILE_SIZE * (1 << zoom)


def lat_to_pixel(lat, zoom):
    normalized = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0
    return normalized * TILE_SIZE * (1 << zoom)


def pixel_to_lon(px, zoom):
    return px / (TILE_SIZE * (1 << zoom)) * 360.0 - 180.0


def pixel_to_lat(py, zoom):
    normalized = 1.0 - 2.0 * py / (TILE_SIZE * (1 << zoom))
    return math.degrees(math.atan(math.sinh(math.pi * normalized)))


def lonlat_to_mercator(lon, lat):
    x = math.radians(lon) * WEB_MERCATOR_RADIUS
    y = math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0)) * WEB_MERCATOR_RADIUS
    return x, y


def snap(value, tolerance=1e-7):
    nearest = round(value)
    return float(nearest) if abs(value - nearest) <= tolerance else value


def plan(bbox, zoom):
    """Tile range plus the crop box, in mosaic pixel coordinates."""
    west, south, east, north = bbox
    world = 1 << zoom
    left, right = snap(lon_to_pixel(west, zoom)), snap(lon_to_pixel(east, zoom))
    top, bottom = snap(lat_to_pixel(north, zoom)), snap(lat_to_pixel(south, zoom))

    # East and south edges are exclusive, so an edge exactly on a tile boundary
    # must not pull in a further tile.
    clamp = lambda v: max(0, min(world - 1, v))
    x_min = clamp(math.floor(left / TILE_SIZE))
    x_max = clamp(math.floor(math.nextafter(right, -math.inf) / TILE_SIZE))
    y_min = clamp(math.floor(top / TILE_SIZE))
    y_max = clamp(math.floor(math.nextafter(bottom, -math.inf) / TILE_SIZE))

    return {
        "zoom": zoom,
        "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max,
        "crop": (left - x_min * TILE_SIZE, top - y_min * TILE_SIZE,
                 right - x_min * TILE_SIZE, bottom - y_min * TILE_SIZE),
        "size": (max(1, math.floor(right - left + 0.5)), max(1, math.floor(bottom - top + 0.5))),
        # The bbox actually covered, after snapping to whole output pixels.
        "pixel_bounds": (left, top, right, bottom),
    }


def cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(Path.home(), ".cache")
    return Path(base) / "mapnik-ground-truth" / "osm-tiles"


def fetch_tile(zoom, x, y, user_agent, delay, timeout):
    path = cache_dir() / str(zoom) / str(x) / "{}.png".format(y)
    if path.exists():
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        TILE_URL.format(z=zoom, x=x, y=y),
        headers={"User-Agent": user_agent, "Accept": "image/png"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
    path.write_bytes(data)
    time.sleep(delay)
    return path, True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    parser.add_argument("--zoom", type=int, required=True)
    parser.add_argument("-o", "--output", default="osm-reference.png")
    parser.add_argument("--user-agent", default="mapnik-visual-ground-truth-demo/1.0 (set a real contact)")
    parser.add_argument("--delay", type=float, default=0.5, help="seconds between tile requests")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-tiles", type=int, default=64, help="refuse to fetch more than this many tiles")
    parser.add_argument("--emit-extent", action="store_true",
                        help="also write <output>.extent.json with the exact Web Mercator extent and size")
    opts = parser.parse_args()

    west, south, east, north = opts.bbox
    if not -180.0 <= west < east <= 180.0:
        sys.exit("bbox longitudes must satisfy -180 <= west < east <= 180")
    if not -MAX_MERCATOR_LATITUDE <= south < north <= MAX_MERCATOR_LATITUDE:
        sys.exit("bbox latitudes are outside the Web Mercator range")
    if not 0 <= opts.zoom <= 19:
        sys.exit("zoom must be between 0 and 19")

    p = plan(opts.bbox, opts.zoom)
    columns = p["x_max"] - p["x_min"] + 1
    rows = p["y_max"] - p["y_min"] + 1
    if columns * rows > opts.max_tiles:
        sys.exit("that bbox needs {} tiles; raise --max-tiles only if you really mean it".format(columns * rows))

    mosaic = Image.new("RGBA", (columns * TILE_SIZE, rows * TILE_SIZE))
    downloaded = 0
    for x in range(p["x_min"], p["x_max"] + 1):
        for y in range(p["y_min"], p["y_max"] + 1):
            try:
                path, fresh = fetch_tile(opts.zoom, x, y, opts.user_agent, opts.delay, opts.timeout)
            except urllib.error.HTTPError as error:
                sys.exit("tile {}/{}/{} failed: {}".format(opts.zoom, x, y, error))
            downloaded += 1 if fresh else 0
            with Image.open(path) as tile:
                mosaic.paste(tile.convert("RGBA"),
                             ((x - p["x_min"]) * TILE_SIZE, (y - p["y_min"]) * TILE_SIZE))

    left, top, right, bottom = p["crop"]
    image = mosaic.crop((int(round(left)), int(round(top)), int(round(right)), int(round(bottom))))
    if image.size != p["size"]:
        image = image.resize(p["size"], Image.LANCZOS)
    image.save(opts.output)

    # The extent the image really covers, after snapping to whole pixels.
    px_left, px_top, px_right, px_bottom = p["pixel_bounds"]
    snapped = (pixel_to_lon(px_left, opts.zoom), pixel_to_lat(px_bottom, opts.zoom),
               pixel_to_lon(px_right, opts.zoom), pixel_to_lat(px_top, opts.zoom))
    merc_min = lonlat_to_mercator(snapped[0], snapped[1])
    merc_max = lonlat_to_mercator(snapped[2], snapped[3])

    print("wrote {} - {}x{} px from {} tile(s), {} newly downloaded".format(
        opts.output, image.size[0], image.size[1], columns * rows, downloaded))
    print("  (c) OpenStreetMap contributors - attribute this image wherever it is shown")
    print("  render the same view with:")
    print("    mapnik-ground-truth-render <style.xml> <prefix> \\")
    print("      --size {} {} \\".format(image.size[0], image.size[1]))
    print("      --extent {:.6f} {:.6f} {:.6f} {:.6f}".format(merc_min[0], merc_min[1], merc_max[0], merc_max[1]))

    if opts.emit_extent:
        meta = {
            "zoom": opts.zoom,
            "size": list(image.size),
            "bbox_wgs84": list(snapped),
            "extent_3857": [merc_min[0], merc_min[1], merc_max[0], merc_max[1]],
            "attribution": "(c) OpenStreetMap contributors",
            "source": TILE_URL,
        }
        path = opts.output + ".extent.json"
        with open(path, "w") as handle:
            json.dump(meta, handle, indent=2)
        print("  wrote {}".format(path))


if __name__ == "__main__":
    main()
