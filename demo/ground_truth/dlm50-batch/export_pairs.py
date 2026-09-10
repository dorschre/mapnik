#!/usr/bin/env python3
"""Export validated batch pairs into a flat, portable PNG/Turtle directory."""

import argparse
import json
import os
from pathlib import Path
import re
import tempfile
import time


def rename_image(text, filename):
    """Change only the generated graph's image IRI, preserving other RDF text."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*-map\.png", filename):
        raise ValueError(f"Unsafe image filename: {filename}")
    prefix = re.search(
        r"@prefix\s+(\w+):\s*<https://w3id\.org/cartograph#>\s*\.", text
    )
    if not prefix:
        raise ValueError("Missing CartoGraph prefix in generated Turtle")
    pattern = re.compile(r"(\b" + re.escape(prefix[1]) + r":imageUrl\s+)<([^>]+)>")
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ValueError(f"Expected one imageUrl IRI, found {len(matches)}")
    match = matches[0]
    if match[2].rsplit("/", 1)[-1] != "map.png":
        raise ValueError(f"Unexpected source image reference: {match[2]}")
    return text[:match.start(2)] + filename + text[match.end(2):]


def write_atomic(path, content):
    if path.exists() and path.read_bytes() == content:
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def export(batch, output, completed, name_prefix=""):
    progress = json.loads((batch / "progress.json").read_text())
    maptrace = "areas" not in progress
    if maptrace:
        items = json.loads((batch / "dataset.json").read_text())["items"]
        areas = []
        for item in items:
            slug = item["slug"]
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", slug):
                raise ValueError(f"Unsafe tile slug: {slug}")
            source = batch / slug
            ready = False
            if (source / "example.json").exists():
                validations = json.loads((source / "validation.json").read_text())
                ready = any(v.get("stage") == "G_F" and v.get("conforms") is True
                            for v in validations)
                if not ready:
                    raise ValueError(f"Final graph validation failed: {slug}")
            areas.append({"name": slug, "status": "ready" if ready else "queued"})
        progress = {"areas": areas, "phase": "Incomplete" if progress.get("errors") else ""}
    names = [area["name"] for area in progress["areas"]]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate area names would overwrite exported pairs")
    output.mkdir(parents=True, exist_ok=True)
    for area in progress["areas"]:
        name = name_prefix + area["name"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
            raise ValueError(f"Unsafe area name: {name}")
        if area["status"] != "ready":
            continue
        if maptrace:
            source = batch / area["name"]
        else:
            source = batch / "public" / area["name"]
            if not source.is_dir():
                source = batch / area["name"] / "dtk50"
        png = source / "map.png"
        ttl = source / ("g_f.ttl" if maptrace else "map.ttl")
        signature = tuple((p.stat().st_size, p.stat().st_mtime_ns) for p in (png, ttl))
        if completed.get(name) == signature:
            continue
        image_name = f"{name}-map.png"
        graph = rename_image(ttl.read_text(encoding="utf-8"), image_name)
        write_atomic(output / image_name, png.read_bytes())
        write_atomic(output / f"{name}-map.ttl", graph.encode("utf-8"))
        completed[name] = signature
        print(f"Exported {name} ({len(completed)}/{len(names)})", flush=True)
    return progress


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--name-prefix", default="", help="Prefix filenames, e.g. nuremberg- for tiles sharing a city")
    parser.add_argument("--watch", action="store_true", help="Export new ready pairs every 30 seconds until the batch finishes")
    args = parser.parse_args()
    completed = {}
    while True:
        progress = export(args.batch.resolve(), args.output.resolve(), completed, args.name_prefix)
        if not args.watch or all(a["status"] == "ready" for a in progress["areas"]):
            print(f"{len(completed)} pairs in {args.output.resolve()}", flush=True)
            return
        if progress.get("phase", "").startswith("Incomplete"):
            raise SystemExit("Batch stopped incomplete; rerun this exporter after resuming generation")
        time.sleep(30)


if __name__ == "__main__":
    main()
