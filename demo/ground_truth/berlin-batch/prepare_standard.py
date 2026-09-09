"""Compile an installed OSM Carto checkout without changing the upstream tree."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from lxml import etree as ET


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--upstream", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    a.upstream = a.upstream.resolve()
    a.output = a.output.resolve()
    a.output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[3]
    project = a.output / "project.mml"
    shutil.copyfile(a.upstream / "project.mml", project)
    for script in ("patch_project.py", "patch_project_queries.py"):
        subprocess.run([sys.executable, str(root / "demo/ground_truth/osm-carto" / script), str(project)], check=True)
    with (a.output / "raw.xml").open("w") as output, (a.output / "compiler.log").open("w") as log:
        subprocess.run(["docker", "run", "--rm", "--network", "none", "-v", f"{a.upstream}:/work:ro",
                        "-v", f"{project}:/work/project.mml:ro", "-w", "/work", "carto-cli:latest",
                        "carto", "project.mml"], stdout=output, stderr=log, check=True)
    tree = ET.parse(str(a.output / "raw.xml"))
    for node in tree.iter():
        for attribute in ("file", "font-directory"):
            value = node.get(attribute)
            if value and value.startswith("/work/"):
                node.set(attribute, str(a.upstream / value[6:]))
            elif value and not Path(value).is_absolute():
                node.set(attribute, str(a.upstream / value))
    tree.write(str(a.output / "style.xml"), encoding="utf-8", xml_declaration=True)
    manifest = dict(upstream=str(a.upstream), revision=subprocess.check_output(
        ["git", "-C", str(a.upstream), "rev-parse", "HEAD"], text=True).strip(),
        project_sha256=hashlib.sha256(project.read_bytes()).hexdigest(),
        stylesheet_sha256=hashlib.sha256((a.output / "style.xml").read_bytes()).hexdigest(),
        modifications="Host-side database connection and osm_id query projection; absolute asset paths")
    (a.output / "manifest.json").write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
