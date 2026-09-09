"""Write shared pixel-aligned areas for rendering and ATKIS acquisition."""
import argparse
import json
import math
from pathlib import Path

DEFAULT_AREAS = [
    dict(name="mitte", label="Berlin Mitte", lon=13.405, lat=52.52, states=["Berlin"]),
    dict(name="charlottenburg", label="Berlin Charlottenburg", lon=13.30, lat=52.52, states=["Berlin"]),
    dict(name="koepenick", label="Berlin Köpenick", lon=13.575, lat=52.445, states=["Berlin"]),
    dict(name="potsdam", label="Potsdam", lon=13.06, lat=52.40, states=["Brandenburg"]),
    dict(name="bernau", label="Bernau bei Berlin", lon=13.587, lat=52.68, states=["Brandenburg"]),
]


def align(area, zoom=15, size=640):
    world = 256 * 2**zoom
    px = round((area["lon"] + 180) / 360 * world)
    py = round((1 - math.asinh(math.tan(math.radians(area["lat"]))) / math.pi) / 2 * world)

    def geographic(x, y):
        return x / world * 360 - 180, math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / world))))

    half = size // 2
    lon, lat = geographic(px, py)
    west, north = geographic(px - half, py - half)
    east, south = geographic(px + half, py + half)
    radius = 6378137
    resolution = 2 * math.pi * radius / world
    cx = math.radians(lon) * radius
    cy = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * radius
    return dict(area, lon=lon, lat=lat, zoom=zoom, size=size, bbox=[west, south, east, north],
                extent_3857=[cx - half * resolution, cy - half * resolution,
                             cx + half * resolution, cy + half * resolution],
                pixel_bounds=[px - half, py - half, px + half, py + half])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output", type=Path)
    p.add_argument("--centers", type=Path, help="JSON list of name, label, lon, lat, states")
    p.add_argument("--zoom", type=int, default=15)
    p.add_argument("--size", type=int, default=640)
    a = p.parse_args()
    if not 12 <= a.zoom <= 17 or not 64 <= a.size <= 2048 or a.size % 2:
        p.error("Use zoom 12–17 and an even size between 64 and 2048")
    centers = json.loads(a.centers.read_text()) if a.centers else DEFAULT_AREAS
    if len({row["name"] for row in centers}) != len(centers):
        p.error("Area names must be unique")
    for row in centers:
        if not row["name"].replace("-", "").replace("_", "").isalnum():
            p.error("Area names may only contain letters, digits, underscores and hyphens")
        if len(row["states"]) != 1 or row["states"][0] not in ("Berlin", "Brandenburg"):
            p.error("Each area must be within one provider region: Berlin or Brandenburg")
        if not -180 < row["lon"] < 180 or not -80 < row["lat"] < 80:
            p.error("Invalid geographic center")
    rows = [align(row, a.zoom, a.size) for row in centers]
    a.output.mkdir(parents=True, exist_ok=True)
    (a.output / "areas.json").write_text(json.dumps(rows, indent=2))
    requests = [dict(**{"bbox-uid": row["name"]}, bbox=row["bbox"], states=row["states"],
                     width_px=row["size"], height_px=row["size"]) for row in rows]
    (a.output / "areas.jsonl").write_text(''.join(json.dumps(row) + '\n' for row in requests))


if __name__ == "__main__":
    main()
