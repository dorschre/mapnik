"""Audit completed outputs, summarize coverage, and build visual-review sheets."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont


def audit(batch, require_complete=False):
    public=batch/'public'
    progress=json.loads((public/'progress.json').read_text())
    areas=json.loads((batch/'areas.json').read_text())
    ready={a['name']:a for a in progress['areas'] if a['status']=='ready'}
    if require_complete and len(ready)!=100: raise ValueError(f'Only {len(ready)}/100 ready')
    prefixes={'NRW':'DENW','Saxony':'DESN','Hesse':'DEHE','Rhineland-Palatinate':'DERP'}
    groups=defaultdict(list);hashes={};rows=[]
    for area in areas:
        name=area['name'];state=area['states'][0]
        if name not in ready: continue
        root=batch/name/'dtk50';truth=json.loads((root/'map.json').read_text())
        validation=json.loads((root/'validation.json').read_text())
        assert validation['shacl_conforms'] and validation['nonblank_image']
        assert validation['visible_objects']==len(truth['objects'])
        assert all(abs(a-b)<1e-6 for a,b in zip(area['extent_3857'],truth['map']['extent']))
        assert all(uri.startswith('https://wunderfacts.com/adv/oid/'+prefixes[state])
                   for uri in truth['source_geometries']),name
        digest=hashlib.sha256((root/'map.png').read_bytes()).hexdigest()
        assert digest not in hashes,(name,hashes.get(digest))
        hashes[digest]=name
        counts=truth['source_snapshot']['observed_feature_counts']
        row=dict(name=name,label=area['label'],state=state,png_sha256=digest,
            visible_objects=len(truth['objects']),source_geometries=len(truth['source_geometries']),
            triples=validation['triples'],buildings=counts.get('AX_Gebaeude',0),
            road_axes=counts.get('AX_Strassenachse',0),contours=counts.get('AX_Hoehenlinie',0),
            preview=truth['coverage']['preview'],source_snapshot_sha256=truth['source_snapshot']['sha256'],
            coverage_issue_counts=dict(Counter(i['kind'] for i in truth['coverage']['issues'])))
        rows.append(row);groups[state].append(area)
    summaries=[]
    for state in prefixes:
        selected=[r for r in rows if r['state']==state]
        summaries.append(dict(state=state,maps=len(selected),triples=sum(r['triples'] for r in selected),
            visible_objects=sum(r['visible_objects'] for r in selected),
            maps_with_buildings=sum(r['buildings']>0 for r in selected),
            maps_with_road_axes=sum(r['road_axes']>0 for r in selected),
            maps_with_contours=sum(r['contours']>0 for r in selected),
            preview_maps=sum(r['preview'] for r in selected)))
    result=dict(checked_at=datetime.now(timezone.utc).isoformat(),ready=len(rows),states=summaries,areas=rows,
                checks='Unique PNGs; state object prefixes; validated graph/object counts; exact map extents; input class counts')
    (public/'coverage-summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    font_path=Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    font=ImageFont.truetype(str(font_path),15) if font_path.exists() else ImageFont.load_default()
    for state,selected in groups.items():
        sheet=Image.new('RGB',(1200,((len(selected)+4)//5)*270+45),'white')
        draw=ImageDraw.Draw(sheet);draw.text((12,10),state+' — generated DTK50 previews',font=font,fill='black')
        for index,area in enumerate(selected):
            x=(index%5)*240;y=(index//5)*270+45
            with Image.open(batch/area['name']/'dtk50/map.png') as image:
                image=image.convert('RGB');image.thumbnail((232,232));sheet.paste(image,(x+4,y))
            draw.text((x+4,y+237),area['label'][:25],font=font,fill='black')
        sheet.save(public/(state.lower()+'-overview.png'))
    print(json.dumps(summaries,indent=2))

if __name__=='__main__': audit(Path(sys.argv[1]).resolve(),'--require-complete' in sys.argv)
