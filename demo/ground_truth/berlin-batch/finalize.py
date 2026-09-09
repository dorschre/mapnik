"""Check finished outputs and write a dataset index and content checksums."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from PIL import Image


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("batch", type=Path)
    a = p.parse_args()
    areas = json.loads((a.batch / "areas.json").read_text())
    rows, checksums = [], {}
    for area in areas:
        for product in ("dtk50", "osm-standard", "opentopomap", "humanitarian"):
            rel = Path(area["name"]) / product
            directory = a.batch / rel
            truth = json.loads((directory / "map.json").read_text())
            validation = json.loads((directory / "validation.json").read_text())
            if not validation["shacl_conforms"]:
                raise ValueError(f"{rel}: graph validation failed")
            row = dict(area=area["name"], label=area["label"], product=product,
                       map=str(rel / "map.png"), ground_truth=str(rel / "map.json"), graph=str(rel / "map.ttl"),
                       bbox_wgs84=area["bbox"], extent_3857=area["extent_3857"], size=[area["size"]]*2,
                       preview=truth.get("coverage", {}).get("preview", False),
                       source_transport=truth.get("source_snapshot", {}).get("transport") if product == "dtk50" else "local-osm-database",
                       visible_objects=len(truth["objects"]), displayed_elements=len(truth["elements"]),
                       rdf_triples=validation["triples"], shacl_conforms=True)
            files = ["map.png", "map.json", "map.ttl", "manifest.json", "validation.json", "shacl-report.txt"]
            if product != "dtk50":
                reference = json.loads((directory / "reference.json").read_text())
                assert reference["extent_3857"] == area["extent_3857"]
                assert reference["pixel_bounds"] == area["pixel_bounds"]
                assert reference["size"] == [area["size"]]*2 and not reference["resampled"]
                with Image.open(directory / "reference.png") as image:
                    assert image.size == (area["size"], area["size"])
                row["reference"] = str(rel / "reference.png")
                row["reference_provenance"] = str(rel / "reference.json")
                files += ["reference.png", "reference.json"]
            else:
                files += ["coverage-report.json"]
            for name in files:
                checksums[str(rel / name)] = hashlib.sha256((directory / name).read_bytes()).hexdigest()
            rows.append(row)
    (a.batch / "dataset.jsonl").write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows))
    summary = dict(created_at=datetime.now(timezone.utc).isoformat(), areas=len(areas), maps=len(rows),
                   knowledge_graphs=len(rows), references=sum("reference" in row for row in rows),
                   incomplete_dtk50_previews=sum(row["preview"] for row in rows),
                   adv_wrapper_maps=sum(row["source_transport"] == "adv-wrapper" for row in rows),
                   visible_objects=sum(row["visible_objects"] for row in rows),
                   displayed_elements=sum(row["displayed_elements"] for row in rows),
                   rdf_triples=sum(row["rdf_triples"] for row in rows), shacl_conforms=True,
                   shacl_inference="none", dataset_index="dataset.jsonl", gallery="index.html",
                   limitation="DTK50 outputs are incomplete Basis-DLM previews; graphs annotate local renders, not references.")
    (a.batch / "manifest.json").write_text(json.dumps(summary, indent=2))
    (a.batch / "SHA256SUMS").write_text(''.join(f'{digest}  {name}\n' for name, digest in sorted(checksums.items())))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
