"""Apply the installed DTK50 catalog to RDF snapshots on a shared Mercator grid.

Run with the gpkg-to-dtk50 virtual environment. Source layers retain EPSG:25832;
Mapnik reprojects them onto the exact OSM/reference viewport in EPSG:3857.
"""
import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
from xml.etree import ElementTree as ET

from map_symbology.mapnik.datasource import write_tile_datasource
from dtk50_stylesheet import build_stylesheet, scale_for_mapnik_viewport
from map_symbology.pipeline import MapRenderRequest, generate_render_tiles
from map_symbology.resource_paths import default_catalog_path, default_text_style_config_path
from map_symbology.sources.rdf import RdfFeatureSource


PRESENTATION_MODE = 'stdpraes_praesobj'


def render(area, batch, allow_preview, snapshots=None, output_root=None, presentation_mode=PRESENTATION_MODE):
    root = Path(__file__).resolve().parents[3]
    snapshot = (snapshots or batch / "snapshots") / area["name"] / area["states"][0]
    if area.get('snapshot_directory'):
        snapshot = batch / area['snapshot_directory']
    metadata = json.loads((snapshot / "manifest.json").read_text())
    source_class = RdfFeatureSource
    if metadata.get("transport") == "adv-wrapper":
        from adv_source import AdvFeatureSource
        source_class = AdvFeatureSource
        # ADV's archived GML literals can omit XML namespace declarations.
        # The adapter validates and repairs these during decoding; suppress
        # rdflib's duplicate conversion tracebacks, not adapter exceptions.
        logging.getLogger('rdflib.term').setLevel(logging.CRITICAL)
    source = source_class(snapshot)
    out = ((output_root or batch) / area["name"] / "dtk50").resolve()
    out.mkdir(parents=True, exist_ok=True)
    request = MapRenderRequest(
        source=source, catalog_path=default_catalog_path(), bbox_wgs84=tuple(area["bbox"]),
        image_size=area["size"], tile_count=1, keep_empty=True, context_buffer_m=250,
        allow_incomplete_preview=allow_preview, include_ap=False, presentation_mode=presentation_mode,
        terrain_zone=area.get('terrain_zone'),
        text_style_config=default_text_style_config_path(),
    )
    try:
        result = generate_render_tiles(request)
    except Exception as error:
        (out / "coverage-report.json").write_text(json.dumps(dict(
            complete=False, error=str(error), issues=getattr(source, "coverage_report", source.diagnostics)), indent=2))
        raise
    tile = result.tiles[0]
    ds = write_tile_datasource(tile.render_objects, tile.extent, out / "datasource", rule_order=result.rule_order)
    buckets = {(o.render_class.geometry_type, o.rule_id): o.render_class for o in tile.render_objects}
    mapnik_scale = scale_for_mapnik_viewport(area, tile.render_scale)
    sheet = build_stylesheet(ds, buckets, image_size=area["size"], render_scale=mapnik_scale,
                             symbol_cache_dir=out / "symbols")
    issues = list(result.coverage_report)
    if 'AX_Hoehenlinie' in source.available_types and not area.get('terrain_zone'):
        issues.append(dict(kind='missing_terrain_context',detail='Contour rules require an explicit terrain_zone'))
    if sheet.unsupported:
        issues.append(dict(kind="unsupported_mapnik_styles", detail=sheet.unsupported))
    report = dict(complete=not issues, preview=bool(issues), issues=issues, provider=source.provider,
                  dataset=source.dataset, source_sha256=source.manifest.get("sha256"),
                  objects=len(tile.render_objects),render_settings=dict(presentation_mode=presentation_mode,
                    include_ap=False,terrain_zone=area.get('terrain_zone'),terrain_source=area.get('terrain_source')),
                  mapnik_render_scale=dict(m_per_px=mapnik_scale.m_per_px,dpi=mapnik_scale.dpi,
                    basis='horizontal pixel at EPSG:3857 viewport centre measured in EPSG:25832'))
    (out / "coverage-report.json").write_text(json.dumps(report, indent=2))
    if issues and not allow_preview:
        raise ValueError("Incomplete DTK50 inputs; see coverage-report.json")
    tree = ET.fromstring(sheet.xml)
    tree.set("srs", "epsg:3857")
    # Each layer's explicit EPSG:25832 is deliberately preserved.
    style = out / "style.xml"
    ET.ElementTree(tree).write(style, encoding="utf-8", xml_declaration=True)
    command = [str(root / "build/out/mapnik-ground-truth-render"), str(style), str(out / "map"),
               "--size", str(area["size"]), str(area["size"]), "--extent", *map(str, area["extent_3857"]),
               "--plugins", str(root / "build/out/plugins/input"), "--simplify", "1"]
    for directory in filter(None, os.environ.get("MAPNIK_FONT_DIRS", "").split(os.pathsep)):
        command += ["--fonts", directory]
    subprocess.run(command, check=True)
    truth = json.loads((out / "map.json").read_text())
    truth.update(source_snapshot=source.manifest, coverage=report, source_geometries=source.source_geometries)
    (out / "map.json").write_text(json.dumps(truth, ensure_ascii=False))
    subprocess.run([str(root / ".venv/bin/python"), str(root / "demo/ground_truth/rdf/ground_truth_to_rdf.py"),
                    str(out / "map.json"), "--image-url", "map.png"], check=True)
    (out / "manifest.json").write_text(json.dumps(dict(product="dtk50", area=area, coverage=report,
        source=dict(transport=metadata.get("transport"), wrapper=metadata.get("wrapper"), snapshot=str(snapshot)),
        catalog=str(default_catalog_path()), attribution=source.manifest.get("attribution")), indent=2))
    print(area["name"], "DTK50", "PREVIEW" if issues else "complete", len(truth["objects"]), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("batch", type=Path)
    p.add_argument("--allow-incomplete-preview", action="store_true")
    p.add_argument("--only", nargs="+")
    p.add_argument("--snapshots", type=Path, help="Alternate snapshot root, e.g. adv-snapshots")
    p.add_argument("--output-root", type=Path, help="Stage replacement maps before updating the gallery")
    p.add_argument("--presentation-mode", choices=['stdpraes', 'stdpraes_praesobj'], default=PRESENTATION_MODE,
                   help="Include catalog presentation symbols and labels with stdpraes_praesobj")
    a = p.parse_args()
    failures = []
    for area in json.loads((a.batch / "areas.json").read_text()):
        if a.only and area["name"] not in a.only:
            continue
        try:
            render(area, a.batch.resolve(), a.allow_incomplete_preview, a.snapshots, a.output_root, a.presentation_mode)
        except Exception as error:
            failures.append(area["name"])
            print(area["name"], "FAILED", repr(error), flush=True)
    if failures:
        raise SystemExit("Failed: " + ", ".join(failures))


if __name__ == "__main__":
    main()
