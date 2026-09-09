"""Re-render frozen DTK50 datasources with Mapnik-only stylesheet corrections.

Run with the companion environment. No source acquisition or rule matching is
performed. Original outputs and companion files are not modified.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from xml.etree import ElementTree as ET

from map_symbology.catalog.extract import load_symbology_catalog
from map_symbology.pipeline.request import MapRenderRequest, _request_tiles, _render_scale_for_tile
from map_symbology.rendering.class_builder import build_render_classes
from map_symbology.rendering.ir_bridge import _style_for_render_class
from map_symbology.resource_paths import default_catalog_path, default_text_style_config_path
from dtk50_stylesheet import correct_tree, symbol_scales, scale_for_mapnik_viewport


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def restyle(batch, output, only=None):
    root = Path(__file__).resolve().parents[3]
    catalog = load_symbology_catalog(default_catalog_path())
    scales = symbol_scales(catalog)
    styles_by_mode = {}
    review = []
    for area in json.loads((batch / 'areas.json').read_text()):
        if only and area['name'] not in only:
            continue
        source = batch / area['name'] / 'dtk50'
        target = output / area['name'] / 'dtk50'
        if source.resolve() == target.resolve():
            raise ValueError('Use a separate output directory to preserve the reviewed originals')
        target.mkdir(parents=True, exist_ok=True)
        original = json.loads((source / 'map.json').read_text())
        mode = original['coverage'].get('render_settings', {}).get('presentation_mode', 'stdpraes')
        if mode not in styles_by_mode:
            classes = build_render_classes(catalog, include_ap=False, presentation_mode=mode,
                                           text_style_config=default_text_style_config_path())
            styles_by_mode[mode] = {(c.geometry_type, r): _style_for_render_class(c, r)
                                    for c in classes for r in c.rule_ids}
        styles = styles_by_mode[mode]
        tree = ET.parse(source / 'style.xml').getroot()
        before_filters = [ET.tostring(e) for e in tree.findall('.//Filter')]
        before_layers = [ET.tostring(e) for e in tree.findall('Layer')]
        data_paths = [Path(e.text) for e in tree.findall(".//Datasource/Parameter[@name='file']")]
        data_hashes = {str(p): sha(p) for p in data_paths}
        request = MapRenderRequest(bbox_wgs84=tuple(area['bbox']), image_size=area['size'], tile_count=1)
        scale = _render_scale_for_tile(request, _request_tiles(request)[0].extent)
        mapnik_scale = scale_for_mapnik_viewport(area, scale)
        previous_scale = mapnik_scale if original['coverage'].get('mapnik_render_scale') else scale
        changes = correct_tree(tree, styles, mapnik_scale, scales, original_scale=previous_scale)
        changes['viewport_scale_ratio'] = mapnik_scale.px_per_mm / scale.px_per_mm
        assert before_filters == [ET.tostring(e) for e in tree.findall('.//Filter')]
        assert before_layers == [ET.tostring(e) for e in tree.findall('Layer')]
        xml = target / 'style.xml'
        ET.ElementTree(tree).write(xml, encoding='utf-8', xml_declaration=True)
        command = [str(root / 'build/out/mapnik-ground-truth-render'), str(xml), str(target / 'map'),
                   '--size', str(area['size']), str(area['size']), '--extent',
                   *map(str, area['extent_3857']), '--plugins', str(root / 'build/out/plugins/input'), '--simplify', '1']
        for directory in filter(None, os.environ.get('MAPNIK_FONT_DIRS', '').split(os.pathsep)):
            command += ['--fonts', directory]
        subprocess.run(command, check=True)
        truth = json.loads((target / 'map.json').read_text())
        for key in ('source_snapshot', 'source_geometries', 'coverage'):
            truth[key] = original[key]
        truth['coverage']['mapnik_stylesheet_corrections'] = changes
        (target / 'map.json').write_text(json.dumps(truth, ensure_ascii=False))
        (target / 'coverage-report.json').write_text(json.dumps(truth['coverage'], indent=2))
        manifest = json.loads((source / 'manifest.json').read_text())
        manifest['coverage'] = truth['coverage']
        manifest['mapnik_stylesheet_source'] = str(source / 'style.xml')
        (target / 'manifest.json').write_text(json.dumps(manifest, indent=2))
        subprocess.run([str(root / '.venv/bin/python'), str(root / 'demo/ground_truth/rdf/ground_truth_to_rdf.py'),
                        str(target / 'map.json'), '--image-url', 'map.png'], check=True)
        for name in ('reference.png', 'reference.json'):
            (target / name).write_bytes((source / name).read_bytes())
        assert data_hashes == {str(p): sha(p) for p in data_paths}
        record = dict(area=area['name'], changes=changes, unchanged_datasources=data_hashes,
                      original_map_sha256=sha(source / 'map.png'), corrected_map_sha256=sha(target / 'map.png'),
                      original_style_sha256=sha(source / 'style.xml'), corrected_style_sha256=sha(xml))
        review.append(record)
        (output / 'stylesheet-changes.json').write_text(json.dumps(review, indent=2))
        print(area['name'], changes, flush=True)
    (output / 'areas.json').write_bytes((batch / 'areas.json').read_bytes())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--only', nargs='+')
    args = parser.parse_args()
    restyle(args.batch.resolve(), args.output.resolve(), args.only)
