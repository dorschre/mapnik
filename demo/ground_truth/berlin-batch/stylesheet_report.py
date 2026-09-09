"""Build a before/reference/after gallery and audit the frozen Mapnik inputs."""
import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
from xml.etree import ElementTree as ET


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(original, corrected):
    records = json.loads((corrected / 'stylesheet-changes.json').read_text())
    areas = {a['name']: a for a in json.loads((corrected / 'areas.json').read_text())}
    cards, audit, checksums = [], [], []
    for record in records:
        name = record['area']
        area = areas[name]
        before = original / name / 'dtk50'
        after = corrected / name / 'dtk50'
        assert sha(before / 'map.png') == record['original_map_sha256']
        assert sha(after / 'map.png') == record['corrected_map_sha256']
        assert sha(after / 'style.xml') == record['corrected_style_sha256']
        assert all(sha(p) == digest for p, digest in record['unchanged_datasources'].items())
        validation = json.loads((after / 'validation.json').read_text())
        assert validation['shacl_conforms']
        reference = json.loads((after / 'reference.json').read_text())
        assert sha(after / 'reference.png') == reference['http']['sha256']
        assert reference['extent'] == area['extent_3857']
        tree = ET.parse(before / 'style.xml')
        inputs = {}
        for node in tree.findall(".//Datasource/Parameter[@name='file']"):
            path = Path(node.text)
            inputs[path] = json.loads(path.read_text())['features']
        # Count each datasource once; many stylesheet layers reuse a file.
        features = [f for values in inputs.values() for f in values]
        rules = Counter(f['properties']['rule_id'] for f in features)
        kinds = Counter(f['properties']['geometry_type'] for f in features)
        feature_types = Counter(f['properties']['feature_type'] for f in features)
        row = dict(area=name, emitted_geometry_counts=dict(kinds),
                   emitted_feature_type_counts=dict(feature_types),
                   emitted_rule_counts=dict(rules),
                   unchanged_datasources_verified=True, shacl_conforms=True,
                   map_sha256=sha(after / 'map.png'), reference_sha256=sha(after / 'reference.png'))
        label_file = before / 'datasource/tile.label.geojson'
        if label_file.exists():
            row['label_datasource_features'] = len(json.loads(label_file.read_text())['features'])
            row['label_datasource_sha256'] = sha(label_file)
        audit.append(row)
        (after / 'before.png').write_bytes((before / 'map.png').read_bytes())
        prefix = f'{name}/dtk50'
        pictures = ''.join(
            f'<figure><figcaption>{label}</figcaption><a href="{prefix}/{file}">'
            f'<img src="{prefix}/{file}" width="640" height="640" alt="{html.escape(area["label"])} — {label}"></a></figure>'
            for label, file in [('Before stylesheet fixes', 'before.png'),
                                ('Official DTK50', 'reference.png'), ('After stylesheet fixes', 'map.png')])
        cards.append(f'<article><h2>{html.escape(area["label"])}</h2><div class="triple">{pictures}</div>'
                     f'<p>{html.escape(reference["source"]["attribution"])}</p>'
                     f'<p><a href="{prefix}/map.ttl">Knowledge graph</a> · '
                     f'<a href="{prefix}/style.xml">Mapnik XML</a> · '
                     f'<a href="{prefix}/coverage-report.json">Input coverage</a> · '
                     f'<a href="{prefix}/validation.json">Graph validation</a></p></article>')
        for file in ('before.png', 'map.png', 'map.json', 'map.ttl', 'style.xml',
                     'manifest.json', 'coverage-report.json', 'reference.png', 'reference.json', 'validation.json'):
            p = after / file
            checksums.append(f'{sha(p)}  {p.relative_to(corrected)}')
    (corrected / 'input-audit.json').write_text(json.dumps(audit, indent=2))
    (corrected / 'SHA256SUMS').write_text('\n'.join(checksums) + '\n')
    document = '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DTK50 — Mapnik stylesheet review</title><style>
body{font:16px/1.5 system-ui,sans-serif;margin:24px;color:#17232d;background:#f4f5f7}
main{max-width:2000px;margin:auto}article{background:white;padding:16px;margin:24px 0}
.triple{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
figure{margin:0}figcaption{font-weight:600;margin:8px 0}img{width:100%;height:auto;display:block}
.notice{background:#fff3d2;padding:18px;border-left:5px solid #b97900}a{color:#075b9c}
@media(max-width:750px){body{margin:12px}.triple{grid-template-columns:1fr}}
</style><main><h1>DTK50 — Mapnik stylesheet review</h1>
<p>Ten maps, the same frozen feature inputs, and new knowledge graphs for the corrected renders.</p>
<p class="notice">These remain incomplete DTK50 previews. The stylesheet fixes address symbol sizing,
rotation, catalog graphic scales, viewport scale and fractional stroke dimensions. Missing streets/buildings/contours, missing labels and
excess supplied symbol features remain upstream limitations. Graph validity does not establish map fidelity.</p>
<p>Click any image to inspect it at its native 640 × 640 resolution.</p>
<p><a href="VISUAL_REVIEW.md">Visual review</a> · <a href="stylesheet-changes.json">Stylesheet changes</a> ·
<a href="input-audit.json">Frozen input audit</a> · <a href="validation.json">Graph checks</a> ·
<a href="SHA256SUMS">Artifact checksums</a></p>'''
    (corrected / 'index.html').write_text(document + ''.join(cards) + '</main></html>')
    print(f'Wrote {len(audit)} comparison triples; verified source hashes and graph checks.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('original', type=Path)
    parser.add_argument('corrected', type=Path)
    args = parser.parse_args()
    build(args.original.resolve(), args.corrected.resolve())
