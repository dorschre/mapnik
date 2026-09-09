"""Build a file-openable paired gallery and coverage comparison for DLM50 states."""
import argparse
from collections import Counter, defaultdict
import hashlib
import html
import json
from pathlib import Path


def build(batch):
    areas = json.loads((batch/'areas.json').read_text())
    rows, groups = [], defaultdict(list)
    checksums = []
    for area in areas:
        directory = batch/area['name']/'dtk50'
        truth = json.loads((directory/'map.json').read_text())
        coverage = json.loads((directory/'coverage-report.json').read_text())
        validation = json.loads((directory/'validation.json').read_text())
        snapshot = truth['source_snapshot']
        issues = coverage['issues']
        row = dict(area=area['name'], label=area['label'], state=area['states'][0], group=area['comparison_group'],
            source_features=sum(snapshot['observed_feature_counts'].values()),
            source_types=snapshot['observed_feature_counts'],
            building_features=snapshot['observed_feature_counts'].get('AX_Gebaeude',0),
            decoded_geometries=len(truth['source_geometries']), visible_objects=len(truth['objects']),
            displayed_elements=len(truth['elements']), triples=validation['triples'],
            shacl_conforms=validation['shacl_conforms'], preview=coverage['preview'],
            unresolved_references=len(snapshot['unresolved_object_references']),
            issue_counts=dict(Counter(i['kind'] for i in issues)),
            rule_failures=[i for i in issues if i['kind']=='unsupported_rule'],
            geometry_failures=dict(Counter(i['detail'] for i in issues if i['kind']=='geometry_or_mapping')),
            render_settings=coverage.get('render_settings',{}),
            road_parents_added=snapshot.get('road_parents_added',0),
            map=f"{area['name']}/dtk50/map.png", graph=f"{area['name']}/dtk50/map.ttl")
        reference_path=directory/'reference.json'
        if reference_path.exists():
            reference=json.loads(reference_path.read_text())
            if reference['crs']!='EPSG:3857' or reference['extent']!=area['extent_3857'] or (reference['width'],reference['height'])!=(area['size'],area['size']):
                raise ValueError('Official reference grid differs from local render: '+area['name'])
            if hashlib.sha256((directory/'reference.png').read_bytes()).hexdigest()!=reference['http']['sha256']:
                raise ValueError('Official reference checksum mismatch: '+area['name'])
            row.update(reference=f"{area['name']}/dtk50/reference.png",
                reference_metadata=f"{area['name']}/dtk50/reference.json",
                reference_source=reference['source'])
        rows.append(row)
        groups[row['group']].append(row)
        files=['map.png','map.json','map.ttl','manifest.json','coverage-report.json','validation.json']
        if 'reference' in row:
            files+=['reference.png','reference.json']
        for name in files:
            p=directory/name
            checksums.append(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(batch)))
    summary={}
    for state in ('NRW','Saxony'):
        selected=[r for r in rows if r['state']==state]
        summary[state]={key:sum(r[key] for r in selected) for key in
            ('source_features','building_features','decoded_geometries','visible_objects','displayed_elements','triples','unresolved_references')}
        counts=Counter()
        failures=Counter()
        types=Counter()
        for row in selected:
            counts.update(row['issue_counts'])
            types.update(row['source_types'])
            for failure in row['rule_failures']:
                failures[failure['detail']]+=failure.get('count',1)
        summary[state].update(issue_counts=dict(counts),rule_failure_cases=dict(failures),
            observed_feature_counts=dict(types),
            maps_with_buildings=sum(r['building_features']>0 for r in selected),
            shacl_conforms=all(r['shacl_conforms'] for r in selected), maps=len(selected),
            official_references=sum('reference' in r for r in selected))
    report=dict(settings=dict(product='dtk50',dataset='dlm50',zoom=14,size=640,scale_denominator=50000,
        context_buffer_m=250,source='https://wunderfacts.com/adv/',preview=True),
        limitations=['Different locations and land cover: counts are descriptive, not proof of state inconsistency.',
        'Counts are summed per area; source features include non-geometric related records, and cross-area objects may repeat.',
            'Standard presentation is used; separate presentation-object symbols require explicit placement data. Regional terrain categories select contour intervals.',
            'Official DTK50 references, when present, use the same requested CRS, extent and size. Their edition dates may differ from the vector snapshots.',
            'Knowledge graphs annotate the local rendered map only, not the official reference raster.',
            'Source totals and attribute schemas are not verified; inspect per-map coverage reports.',
            'Missing GML namespaces, AdV CRS/unit aliases, and structured place names decoded consistently; raw RDF retained.',
            'Both dumps currently use ADV region nrw; requested state checked against DENW/DESN object IDs.'],
        states=summary,maps=rows)
    (batch/'comparison.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
    (batch/'dataset.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
    (batch/'SHA256SUMS').write_text('\n'.join(checksums)+'\n')
    cards=[]
    for group,pair in groups.items():
        cards.append('<section><h2>'+html.escape(group.replace('-',' ').title())+'</h2><div class="pair">')
        for row in pair:
            base=row['area']+'/dtk50/'
            if 'reference' in row:
                visual=(f'<div class="compare"><img src="{row["reference"]}" width="640" height="640" alt="Official DTK50 of {html.escape(row["label"])}">'
                    f'<img class="local" src="{row["map"]}" width="640" height="640" alt="Local DTK50 preview of {html.escape(row["label"])}" style="clip-path:inset(0 50% 0 0)"></div>'
                    f'<label class="slider">Official <input type="range" min="0" max="100" value="50" aria-label="Local preview share for {html.escape(row["label"])}" '
                    'oninput="this.closest(\'article\').querySelector(\'.local\').style.clipPath=\'inset(0 \'+(100-this.value)+\'% 0 0)\'"> Local preview</label>'
                    f'<p><a href="{row["reference"]}">Official PNG</a> · <a href="{row["map"]}">Local PNG</a> · '
                    f'<a href="{row["reference_metadata"]}">Reference source &amp; extent</a></p>'
                    f'<p class="source">{html.escape(row["reference_source"]["attribution"])}</p>')
            else:
                visual=f'<a href="{row["map"]}"><img src="{row["map"]}" width="640" height="640" alt="DTK50 preview of {html.escape(row["label"])}"></a>'
            cards.append(f'<article><h3>{html.escape(row["label"])} · {row["state"]}</h3>'+visual+
                f'<p>{row["visible_objects"]:,} visible objects · {row["unresolved_references"]:,} unresolved references</p>'
                f'<p><a href="{row["graph"]}">Knowledge graph</a> · <a href="{base}map.json">JSON</a> · '
                f'<a href="{base}coverage-report.json">Coverage</a> · <a href="{base}validation.json">Validation</a></p>'
                f'<details><summary>Diagnostics</summary><pre>{html.escape(json.dumps(dict(issues=row["issue_counts"],rule_failures=row["rule_failures"],geometry_failures=row["geometry_failures"]),indent=2))}</pre></details></article>')
        cards.append('</div></section>')
    table='<table><tr><th>Metric</th><th>NRW</th><th>Saxony</th></tr>'
    for key in ('official_references','source_features','building_features','decoded_geometries','visible_objects','unresolved_references','triples','shacl_conforms'):
        table+=f'<tr><th>{key.replace("_"," ")}</th><td>{summary["NRW"][key]}</td><td>{summary["Saxony"][key]}</td></tr>'
    table+='</table>'
    errors=sorted(set(summary['NRW']['rule_failure_cases']) | set(summary['Saxony']['rule_failure_cases']))
    table+='<h2>Rule evaluation failures</h2><p>Counts are failed rule evaluations; one feature can trigger multiple failures.</p><table><tr><th>Failure</th><th>NRW</th><th>Saxony</th></tr>'
    for error in errors:
        table+=f'<tr><th>{html.escape(error)}</th><td>{summary["NRW"]["rule_failure_cases"].get(error,0)}</td><td>{summary["Saxony"]["rule_failure_cases"].get(error,0)}</td></tr>'
    table+='</table>'
    document='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DTK50 — NRW and Saxony</title><style>
body{font:16px/1.5 system-ui,sans-serif;background:#f4f5f7;color:#17232d;margin:0;padding:24px}main{max-width:1360px;margin:auto}
h1{margin-bottom:8px}.notice{background:#fff3d2;padding:18px;border-left:5px solid #b97900}.pair{display:grid;grid-template-columns:1fr 1fr;gap:24px}
article{background:white;padding:16px;border:1px solid #d5dae0;border-radius:8px}img{display:block;width:100%;height:auto}a{color:#075b9c}
.compare{position:relative}.compare .local{position:absolute;inset:0;width:100%;height:100%}.slider{display:flex;align-items:center;gap:10px;margin-top:12px}.slider input{flex:1;min-width:40px}.source{font-size:13px;color:#48545f}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}table{border-collapse:collapse;background:white;margin:24px 0}td,th{text-align:left;border:1px solid #ccd3db;padding:8px 18px}
@media(max-width:700px){body{padding:12px}.pair{grid-template-columns:1fr}td,th{padding:6px;font-size:13px}}
</style><main><h1>DTK50: NRW and Saxony</h1><p>Five areas per state · ADV DLM50 · 640 × 640 · zoom 14 · catalog scale 1:50,000</p>
<p class="notice">Incomplete previews. Identical rendering settings; different geographic locations. Differences in counts alone do not demonstrate inconsistent state data. Structural graph validation does not establish cartographic fidelity.</p>
<p>Use each slider to compare the local preview with its official DTK50 reference. Knowledge graphs describe the local preview only.</p>
<p><a href="comparison.json">Full comparison report</a> · <a href="dataset.jsonl">Dataset index</a> · <a href="adv-snapshots/acquisition-report.json">Vector acquisition</a> · <a href="reference-acquisition.json">Official reference acquisition</a></p>'''
    document+='<h2>Observed input difference</h2><p>Building objects (AX_Gebaeude) occur in '
    document+=f'{summary["NRW"]["maps_with_buildings"]} of 5 NRW samples and {summary["Saxony"]["maps_with_buildings"]} of 5 Saxony samples. '
    document+='This describes the acquired ADV responses; it does not establish that the original state datasets are incomplete.</p>'
    if (batch/'corrections.json').exists():
        document+='<p><a href="CORRECTIONS.md">Rendering corrections and before/after counts</a> · <a href="corrections.json">Correction metrics</a>. Standard presentation permissions and terrain context are now configured. Wermsdorf also uses retrieved road-parent classifications; other areas still report missing parents.</p>'
    document+='<details><summary>Counts, rule failures, and comparison limits</summary>'+table+'<h2>Comparison limits</h2><ul>'+''.join('<li>'+html.escape(v)+'</li>' for v in report['limitations'])+'</ul></details>'
    document+=''.join(cards)+'<p>Data: official NRW and Saxony DLM50 accessed via Linked AdV. Source provenance is retained in each knowledge graph.</p></main></html>'
    (batch/'index.html').write_text(document)
    (batch/'README.md').write_text(
        '# NRW / Saxony DTK50 comparison\n\n'
        'Open `index.html` for five paired views (ten maps and ten knowledge graphs). '
        'Inputs are ADV DLM50 snapshots. Every map uses 640 × 640 pixels, zoom 14, '
        'catalog scale 1:50,000, and a 250 m source buffer.\n\n'
        'Local renders are explicitly incomplete previews; graph conformance does not '
        'establish official DTK50 visual fidelity. See `comparison.json` for aggregate '
        'counts, source differences, rule failures, and comparison limits.\n\n'
        'Per area: `<area>/dtk50/map.png`, `map.json`, `map.ttl`, '
        '`coverage-report.json`, and `validation.json`. '
        'Official references, when available, are `reference.png` and `reference.json`; '
        'each gallery slider compares the exact shared map extent. The local graph does not annotate the official raster. '
        'Raw vector responses and acquisition metadata are under `adv-snapshots/`; '
        'official raster responses and HTTP metadata are under `reference-cache/`. '
        '`dataset.jsonl` indexes outputs; `SHA256SUMS` verifies the principal artifacts.\n\n'
        'Reproduction instructions: '
        '[repository README](../../demo/ground_truth/berlin-batch/README.md#nrw--saxony-dlm50-comparison).\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch',type=Path)
    build(parser.parse_args().batch.resolve())
