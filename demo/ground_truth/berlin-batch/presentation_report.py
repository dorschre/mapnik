"""Build focused comparisons for the Dresden and Cologne presentation defects."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import shutil
from collections import Counter
from xml.etree import ElementTree as ET
from PIL import Image


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(before, batch):
    rows=[];cards=[];checksums=[]
    for area in json.loads((batch/'areas.json').read_text()):
        name=area['name'];out=batch/name/'dtk50';old=before/name/'dtk50'
        previous=json.loads((old/'map.json').read_text());current=json.loads((out/'map.json').read_text())
        assert previous['source_geometries']==current['source_geometries']
        assert previous['source_snapshot']['sha256']==current['source_snapshot']['sha256']
        assert previous['map']['extent']==current['map']['extent']
        shutil.copy2(old/'map.png',out/'before.png')
        def counts(truth):
            return Counter(o['source_properties']['rule_id'] for o in truth['objects']
                           if o['source_properties']['geometry_type']=='point')
        a,b=counts(previous),counts(current)
        row=dict(area=name,source_unchanged=True,
                 point_rules_before=dict(a),point_rules_after=dict(b),
                 stadiums=b['RUL04557'],small_sports_fields=b['RUL04550'],federal_road_shields=b['RUL00950'],
                 map_sha256=sha(out/'map.png'),reference_sha256=sha(out/'reference.png'),
                 presentation_mode=current['coverage']['render_settings']['presentation_mode'],
                 mapnik_source_size_fixes=sum('[size_w_px]' in marker.get('transform','')
                    for marker in ET.parse(out/'style.xml').iter('MarkersSymbolizer')))
        rows.append(row)
        base=name+'/dtk50/'
        panels=''.join(f'<figure><figcaption>{label}</figcaption><a href="{base}{filename}"><img src="{base}{filename}" alt="{html.escape(area["label"])} {label}"></a></figure>'
                       for label,filename in [('Previous','before.png'),('Official','reference.png'),('Configuration + SVG fix','map.png')])
        cards.append(f'<section><h2>{html.escape(area["label"])}</h2><div class="maps">'+panels+'</div><p>'+
                     ' · '.join(f'<a href="{base}{f}">{label}</a>' for label,f in [('Knowledge graph','map.ttl'),('Ground truth','map.json'),('Coverage','coverage-report.json'),('Validation','validation.json')])+'</p></section>')
        for filename in ['map.png','map.json','map.ttl','style.xml','manifest.json','reference.png','reference.json','before.png','coverage-report.json']:
            path=out/filename;checksums.append(sha(path)+'  '+str(path.relative_to(batch)))
    crop_cards=[]
    for name,title,box in [('dresden','Rudolf-Harbig-Stadion',(416,457,485,536)),
                           ('dresden','Heinz-Steyer-Stadion and sports fields',(113,0,249,151)),
                           ('cologne','Railway approach to the central station',(202,111,365,310))]:
        panels=[]
        for label,filename in [('Previous','before.png'),('Official','reference.png'),('Configuration + SVG fix','map.png')]:
            relative=Path(name)/'dtk50'/(Path(filename).stem+'-crop-'+str(box[0])+'.png')
            with Image.open(batch/name/'dtk50'/filename) as image:image.crop(box).save(batch/relative)
            panels.append(f'<figure><figcaption>{label}</figcaption><img class="crop" src="{relative}" alt="{html.escape(title)} {label}"></figure>')
            checksums.append(sha(batch/relative)+'  '+str(relative))
        crop_cards.append('<section><h2>'+html.escape(title)+'</h2><div class="maps">'+''.join(panels)+'</div></section>')
    (batch/'presentation-comparison.json').write_text(json.dumps(rows,indent=2))
    (batch/'SHA256SUMS').write_text('\n'.join(checksums)+'\n')
    (batch/'index.html').write_text('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DTK50 presentation and SVG fixes</title>
<style>body{font:16px/1.5 system-ui;background:#f4f5f7;color:#17232d;margin:24px}main{max-width:1920px;margin:auto}section{background:white;padding:16px;margin:24px 0}.maps{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}figure{margin:0}img{width:100%;display:block}.crop{max-width:450px;image-rendering:pixelated}a{color:#075b9c}.notice{background:#fff3d2;padding:16px}@media(max-width:800px){.maps{grid-template-columns:1fr}body{margin:12px}}</style>
<main><h1>DTK50: missing presentation symbols and SVG sizing</h1>
<p>The previous render configuration excluded presentation entries. The combined mode restores stadiums, sports fields, road-number shields and railway detail symbols. A Mapnik transform fix prevents source-sized SVG strokes from enlarging the requested dimensions.</p>
<p class="notice">Still incomplete: shields and some labels overlap; sports-field sizes/orientations follow the existing pipeline's context fitting; Cologne's railway fan contains overlapping input lines. NRW buildings remain absent. No gpkg-to-dtk50 logic was changed. These are reviewed examples, not accepted official-map reproductions.</p>
<p><a href="FINDINGS.md">Detailed findings and remaining causes</a> · <a href="presentation-comparison.json">Symbol counts</a> · <a href="SHA256SUMS">Checksums</a></p>'''+''.join(crop_cards)+''.join(cards)+'</main></html>')
    print(json.dumps(rows,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('before',type=Path);p.add_argument('batch',type=Path)
    a=p.parse_args();build(a.before.resolve(),a.batch.resolve())
