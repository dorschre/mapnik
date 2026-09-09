"""Fetch the five requested comparison views and crop tiles on exact pixel edges.

This is a bounded review download, not the acquisition path for a 100-map dataset.
Keep the cache and attribution; arrange a suitable service for larger batches.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import time
import urllib.request

from PIL import Image

SERVICES = {
    "osm-standard": ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", "© OpenStreetMap contributors"),
    "opentopomap": ("https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
                   "© OpenStreetMap contributors, SRTM | Map style: © OpenTopoMap (CC-BY-SA)"),
    "humanitarian": ("https://a.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png",
                     "© OpenStreetMap contributors | Humanitarian style: HOT; tiles: OpenStreetMap France"),
}


def tile_plan(bounds):
    left, top, right, bottom = bounds
    if any(int(v) != v for v in bounds) or right <= left or bottom <= top:
        raise ValueError("Reference bounds must lie on integer global pixel edges")
    return range(left // 256, (right - 1) // 256 + 1), range(top // 256, (bottom - 1) // 256 + 1)


def fetch(area, product, batch):
    template, attribution = SERVICES[product]
    out = batch / area["name"] / product
    out.mkdir(parents=True, exist_ok=True)
    xs, ys = tile_plan(area["pixel_bounds"])
    if len(xs) * len(ys) > 16:
        raise ValueError("Review view exceeds 16 tiles")
    mosaic = Image.new("RGB", (len(xs) * 256, len(ys) * 256))
    tiles = []
    for y in ys:
        for x in xs:
            url = template.format(z=area["zoom"], x=x, y=y)
            cache = batch / "reference-cache" / product / str(area["zoom"]) / str(x) / f"{y}.png"
            if not cache.exists():
                request = urllib.request.Request(url, headers={"User-Agent": "Mapnik-Berlin-Comparison/1.0", "Accept": "image/png"})
                with urllib.request.urlopen(request, timeout=45) as response:
                    data = response.read()
                    headers = dict(response.headers)
                with Image.open(io.BytesIO(data)) as image:
                    image.load()
                    if image.size != (256, 256):
                        raise ValueError(f"Unexpected tile size from {url}")
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(data)
                cache.with_suffix(".http.json").write_text(json.dumps(dict(url=url,
                    retrieved_at=datetime.now(timezone.utc).isoformat(), headers=headers), indent=2))
                time.sleep(1)
            with Image.open(cache) as tile:
                mosaic.paste(tile.convert("RGB"), ((x - xs.start) * 256, (y - ys.start) * 256))
            tiles.append(dict(url=url, sha256=hashlib.sha256(cache.read_bytes()).hexdigest(),
                              cache=str(cache.relative_to(batch))))
    left, top, right, bottom = area["pixel_bounds"]
    crop = (left - xs.start * 256, top - ys.start * 256, right - xs.start * 256, bottom - ys.start * 256)
    image = mosaic.crop(crop)
    if image.size != (area["size"], area["size"]):
        raise ValueError("Reference dimensions do not match the rendered map")
    image.save(out / "reference.png")
    metadata = dict(product=product, source=template, attribution=attribution, tiles=tiles,
                    zoom=area["zoom"], size=list(image.size), bbox_wgs84=area["bbox"],
                    extent_3857=area["extent_3857"], pixel_bounds=area["pixel_bounds"],
                    crop=list(crop), resampled=False, reference_only=True)
    (out / "reference.json").write_text(json.dumps(metadata, indent=2))
    print(area["name"], product, len(tiles), "tiles, exact crop", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("batch", type=Path)
    p.add_argument("--product", choices=list(SERVICES), nargs="+", default=list(SERVICES))
    a = p.parse_args()
    areas = json.loads((a.batch / "areas.json").read_text())
    if len(areas) > 5:
        p.error("This downloader is limited to the five requested review views")
    failures = []
    for product in a.product:
        for area in areas:
            try:
                fetch(area, product, a.batch)
            except Exception as error:
                failures.append(dict(area=area["name"], product=product, error=str(error)))
                print("FAILED", failures[-1], flush=True)
    (a.batch / "reference-failures.json").write_text(json.dumps(failures, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
