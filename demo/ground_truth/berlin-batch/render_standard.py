"""Render shared batch areas with an already compiled OSM Standard style."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("batch", type=Path)
    p.add_argument("--style", type=Path, required=True)
    p.add_argument("--fonts", type=Path, required=True)
    a = p.parse_args()
    root = Path(__file__).resolve().parents[3]
    for area in json.loads((a.batch / "areas.json").read_text()):
        out = (a.batch / area["name"] / "osm-standard").resolve()
        out.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            str(root / "build/out/mapnik-ground-truth-render"), str(a.style.resolve()), str(out / "map"),
            "--size", str(area["size"]), str(area["size"]), "--extent", *map(str, area["extent_3857"]),
            "--plugins", str(root / "build/out/plugins/input"), "--fonts", str(a.fonts),
            "--identity", "osm_id", "--simplify", "1",
        ], check=True)
        truth = json.loads((out / "map.json").read_text())
        missing = [o["identity"] for o in truth["objects"] if "osm_id" not in o["identity"]]
        if not truth["objects"]:
            raise ValueError("Empty OSM Standard render")
        manifest = dict(product="osm-standard", area=area, stylesheet=str(a.style.resolve()),
                        visible_objects=len(truth["objects"]), missing_osm_identity=missing,
                        attribution="© OpenStreetMap contributors (ODbL); OpenStreetMap Carto style (CC0)")
        style_manifest = a.style.parent / "manifest.json"
        if style_manifest.exists():
            manifest["style_provenance"] = json.loads(style_manifest.read_text())
        manifest["database_snapshot"] = "Existing local gis database; source replication timestamp not verified"
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
        subprocess.run([str(root / ".venv/bin/python"), str(root / "demo/ground_truth/rdf/ground_truth_to_rdf.py"),
                        str(out / "map.json"), "--zoom", str(area["zoom"])], check=True)
        print(area["name"], "finished; missing identities:", len(missing), flush=True)


if __name__ == "__main__":
    main()
