#!/usr/bin/env python3
"""Render OpenTopoMap/Humanitarian PNG, ground truth and linked Turtle RDF."""
import argparse
from collections import Counter
import hashlib
import html
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
TITLES = {'opentopomap': 'OpenTopoMap', 'humanitarian': 'Humanitarian OSM'}


def enrich_and_check(document, manifest):
    """GDAL has no feature attributes; attach the known raster's content identity."""
    rasters = {row['layer']: row for row in manifest['layers'] if row['source'] == 'elevation raster'}
    counts = Counter()
    for obj in document['objects']:
        identity = obj['identity']
        layer = identity['layer']
        if layer in rasters:
            digest = hashlib.sha256(Path(rasters[layer]['file']).read_bytes()).hexdigest()
            identity['source_id'] = 'dem:raster:' + digest
        if not identity.get('source_id'):
            raise ValueError(f'{layer}: missing source identity')
        if identity['source_id'].startswith('osm:'):
            if identity.get('osm_type') not in ('node', 'way', 'relation') or not isinstance(identity.get('osm_id'), int):
                raise ValueError(f'{layer}: invalid OSM identity')
            counts['osm_objects'] += 1
        else:
            counts['derived_objects'] += 1
        counts['visible_objects'] += 1
    if not counts['osm_objects']:
        raise ValueError('Render contains no visible OSM objects')
    return dict(counts)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--product', choices=[*TITLES, 'both'], default='both')
    p.add_argument('--center', type=float, nargs=2, metavar=('LON', 'LAT'), required=True)
    p.add_argument('--zoom', type=int, default=15)
    p.add_argument('--size', type=int, default=768)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--upstream', type=Path, default=ROOT/'build/map-products/upstream')
    p.add_argument('--cache', type=Path, default=ROOT/'build/map-products/dem-cache')
    p.add_argument('--renderer', type=Path, default=ROOT/'build/out/mapnik-ground-truth-render')
    p.add_argument('--rdf-python', type=Path, default=ROOT/'.venv/bin/python')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=54321)
    p.add_argument('--database', default='gis')
    p.add_argument('--user', default='postgres')
    a = p.parse_args()
    if not 12 <= a.zoom <= 17 or not 64 <= a.size <= 2048:
        p.error('Use zoom 12–17 and size 64–2048')
    if not -180 < a.center[0] < 180 or not -80 < a.center[1] < 80:
        p.error('Center must be within longitude ±180 and latitude ±80')
    a.output = a.output.resolve(); a.output.mkdir(parents=True, exist_ok=True)
    a.upstream = a.upstream.resolve()
    common = ['--center', *map(str, a.center), '--zoom', str(a.zoom), '--size', str(a.size)]
    products = list(TITLES) if a.product == 'both' else [a.product]
    terrain = a.output/'terrain'
    if 'opentopomap' in products:
        subprocess.run([sys.executable, str(HERE/'terrain.py'), *common, '--output', str(terrain),
                        '--cache', str(a.cache), '--upstream', str(a.upstream/'opentopomap')], check=True)
    summaries = []
    for product in products:
        out = a.output/product
        command = [sys.executable, str(HERE/'prepare.py'), '--product', product,
                   '--upstream', str(a.upstream/product), '--output', str(out), *common,
                   '--tag-style', str(a.upstream/'opentopomap/mapnik/osm2pgsql/opentopomap.style'),
                   '--host', a.host, '--port', str(a.port), '--database', a.database, '--user', a.user]
        if product == 'opentopomap':
            command += ['--terrain', str(terrain)]
        subprocess.run(command, check=True)
        manifest = json.loads((out/'manifest.json').read_text())
        fonts = (a.upstream/'humanitarian/fonts' if product == 'humanitarian'
                 else ROOT/'fonts/dejavu-fonts-ttf-2.37/ttf')
        environment = dict(os.environ)
        environment['LD_LIBRARY_PATH'] = str(a.renderer.resolve().parent) + ':' + environment.get('LD_LIBRARY_PATH', '')
        subprocess.run([str(a.renderer), str(out/'style.xml'), str(out/'map'), '--extent',
                        *map(str, manifest['extent']), '--size', str(a.size), str(a.size),
                        '--plugins', str(a.renderer.resolve().parent/'plugins/input'), '--fonts', str(fonts),
                        '--identity', 'osm_id', '--identity', 'osm_type', '--identity', 'source_id',
                        '--no-attributes', '--simplify', '1'], check=True, env=environment)
        document = json.loads((out/'map.json').read_text())
        manifest['verification'] = enrich_and_check(document, manifest)
        (out/'map.json').write_text(json.dumps(document))
        subprocess.run([str(a.rdf_python), str(HERE.parent/'rdf/ground_truth_to_rdf.py'),
                        str(out/'map.json'), '--zoom', str(a.zoom)], check=True)
        manifest['attribution'] = ('© OpenStreetMap contributors (ODbL). '
            + ('Style: OpenTopoMap (CC BY-SA 3.0). Elevation: Mapzen terrain tiles; see ../terrain/terrain.json.'
               if product == 'opentopomap' else 'Style: HOT HDM-CartoCSS (CC0); see upstream LICENCE.txt.'))
        (out/'manifest.json').write_text(json.dumps(manifest, indent=2))
        summaries.append(f'<article><h2>{TITLES[product]}</h2><a href="{product}/map.png">'
            f'<img src="{product}/map.png" alt="{TITLES[product]} map"></a><p>'
            f'<a href="{product}/map.json">Ground truth</a> · <a href="{product}/map.ttl">RDF</a> · '
            f'<a href="{product}/manifest.json">Provenance and checks</a></p>'
            f'<p>{html.escape(manifest["attribution"])}</p></article>')
    (a.output/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>Map products</title>'
        '<style>body{font:16px system-ui;margin:24px}main{display:flex;flex-wrap:wrap;gap:24px}'
        'article{flex:1;min-width:320px}img{width:100%;max-width:768px}p{max-width:768px}</style>'
        f'<h1>Map products</h1><p>Center {a.center[0]}, {a.center[1]} · Zoom {a.zoom}</p>'
        '<main>'+''.join(summaries)+'</main>')
    print('Review:', a.output/'index.html')


if __name__ == '__main__':
    main()
