"""Compare repaired road acquisition with the previous Mapnik renders."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import shutil
from collections import Counter


def visible_sources(truth, feature_type):
    return {uri for obj in truth['objects']
            if obj['source_properties'].get('feature_type') == feature_type
            for uri in json.loads(obj['source_properties']['source_uris'])}


def build(before, batch):
    areas = json.loads((batch/'areas.json').read_text())
    repair = {r['area']: r for r in json.loads((batch/'acquisition-repair.json').read_text())}
    rows, cards, checksums = [], [], []
    for area in areas:
        name = area['name']; out = batch/name/'dtk50'; old = before/name/'dtk50'
        previous = json.loads((old/'map.json').read_text())
        current = json.loads((out/'map.json').read_text())
        validation = json.loads((out/'validation.json').read_text())
        assert validation['shacl_conforms'], name+' graph validation failed'
        assert current['source_geometries'] == previous['source_geometries'], name+' geometric inputs changed'
        assert current['map']['extent'] == previous['map']['extent']
        # C++ JSON serialization can differ from the requested double by an ULP.
        assert max(abs(x-y) for x,y in zip(current['map']['extent'],area['extent_3857'])) < 1e-6
        closure = current['source_snapshot']['road_dependency_acquisition']
        assert closure['complete'] and closure['missing_parents'] == 0
        assert repair[name]['referenced_parents'] == closure['referenced_parents']
        for filename in ('reference.png','reference.json'):
            shutil.copy2(old/filename, out/filename)
        shutil.copy2(old/'map.png', out/'before.png')
        reference = json.loads((out/'reference.json').read_text())
        assert reference['extent'] == area['extent_3857'] and reference['crs'] == 'EPSG:3857'
        assert (reference['width'],reference['height']) == (area['size'],area['size'])
        assert hashlib.sha256((out/'reference.png').read_bytes()).hexdigest() == reference['http']['sha256']
        counts = current['source_snapshot']['observed_feature_counts']
        row = dict(area=name,state=area['states'][0],**closure,
                   visible_road_axes_before=len(visible_sources(previous,'AX_Strassenachse')),
                   visible_road_axes_after=len(visible_sources(current,'AX_Strassenachse')),
                   building_inputs=counts.get('AX_Gebaeude',0),contour_inputs=counts.get('AX_Hoehenlinie',0),
                   geometric_inputs_unchanged=True,shacl_conforms=True,
                   visible_types=dict(Counter(o['source_properties'].get('feature_type') for o in current['objects'])),
                   map_sha256=hashlib.sha256((out/'map.png').read_bytes()).hexdigest(),
                   source_sha256=current['source_snapshot']['sha256'])
        rows.append(row)
        base=name+'/dtk50/'
        panels=''.join(f'<figure><figcaption>{label}</figcaption><a href="{base}{file}"><img width="640" height="640" src="{base}{file}" alt="{html.escape(area["label"])} {label}"></a></figure>'
                       for label,file in [('Before','before.png'),('Official','reference.png'),('Repaired acquisition','map.png')])
        cards.append(f'<section><h2>{html.escape(area["label"])} · {row["state"]}</h2><p>{closure["referenced_parents"]} verified road parents; '
                     f'visible road axes: {row["visible_road_axes_before"]} → {row["visible_road_axes_after"]}.</p><div class="maps">'+panels+'</div><p>'+
                     ' · '.join(f'<a href="{base}{file}">{label}</a>' for label,file in [('Knowledge graph','map.ttl'),('JSON','map.json'),('Coverage','coverage-report.json'),('Validation','validation.json'),('Reference provenance','reference.json')])+
                     '</p><p>'+html.escape(reference['source']['attribution'])+'</p></section>')
        for filename in ('map.png','map.json','map.ttl','style.xml','manifest.json','coverage-report.json','validation.json','reference.png','reference.json','before.png'):
            path=out/filename
            checksums.append(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+str(path.relative_to(batch)))
    report=dict(maps=rows,road_parents=sum(r['referenced_parents'] for r in rows),
                downloaded_parents=sum(r['added_this_run'] for r in rows),
                limitations=['NRW snapshots still contain no building or contour objects; see availability probes.',
                             'Labels and symbol placement still reflect unchanged gpkg-to-dtk50 behavior.',
                             'Other unresolved relationships and rule evaluation failures remain in coverage reports.',
                             'Graph validation does not establish official cartographic fidelity.'])
    (batch/'acquisition-comparison.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
    (batch/'SHA256SUMS').write_text('\n'.join(checksums)+'\n')
    extra_links = '<p>'+' · '.join(
        f'<a href="{filename}">{label}</a>' for filename,label in
        [('VISUAL_REVIEW.md','Visual review'),('road-input-audit.json','Road input audit'),
         ('availability-probes/README.md','Remaining NRW source gap')]
        if (batch/filename).exists())+'</p>'
    (batch/'index.html').write_text('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DTK50 acquisition repair</title><style>body{font:16px/1.5 system-ui;margin:24px;background:#f4f5f7;color:#17232d}main{max-width:1950px;margin:auto}section{background:white;margin:24px 0;padding:16px}.maps{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}figure{margin:0}img{width:100%;height:auto;display:block}a{color:#075b9c}figcaption{font-weight:bold}.notice{padding:16px;background:#fff3d2}@media(max-width:850px){body{margin:12px}.maps{grid-template-columns:1fr}}</style>
<main><h1>DTK50 road acquisition repair</h1><p>Five areas each in NRW and Saxony. Identical geometric inputs, map extent, and Mapnik stylesheet settings; missing road-parent records retrieved through ADV.</p>
<p class="notice">Incomplete DTK50 previews: NRW buildings/contours remain absent from the acquired source; missing labels and excessive symbols remain visible. The knowledge graphs describe the local images only. Structural validation is separate from visual fidelity.</p>
<p><a href="acquisition-comparison.json">Before/after metrics</a> · <a href="acquisition-repair.json">Acquisition results</a> · <a href="SHA256SUMS">Checksums</a></p>'''+extra_links+''.join(cards)+'</main></html>')
    (batch/'README.md').write_text(
        '# DTK50 road acquisition repair\n\n'
        'Open `index.html` for ten before/official/after comparisons and links to the regenerated knowledge graphs. '
        '`acquisition-comparison.json` records visible road counts, source hashes and unchanged geometric inputs. '
        '`acquisition-repair.json` records retrieved parents; `snapshots/` contains the repaired RDF; '
        '`request-cache/` retains raw parent responses.\n\n'
        'All referenced road parents are present. These remain incomplete DTK50 previews: '
        'NRW buildings/contours, labels, symbol placement and other coverage issues remain. '
        'Graph validation does not establish visual fidelity.\n\n'
        'See [reproduction instructions](../../demo/ground_truth/berlin-batch/README.md#repairing-road-acquisition).\n')
    print(json.dumps(dict(maps=len(rows),road_parents=report['road_parents'],downloaded_parents=report['downloaded_parents'])))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before',type=Path);parser.add_argument('batch',type=Path)
    args=parser.parse_args();build(args.before.resolve(),args.batch.resolve())
