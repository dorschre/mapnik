"""Build a file-openable comparison gallery with links to every knowledge graph."""
import argparse
import html
import json
from pathlib import Path

PRODUCTS = {"osm-standard": "OSM Standard", "opentopomap": "OpenTopoMap",
            "humanitarian": "Humanitarian OSM", "dtk50": "DTK50"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("batch", type=Path)
    a = p.parse_args()
    areas = json.loads((a.batch / "areas.json").read_text())
    adv_report_path = a.batch / "adv-snapshots/acquisition-report.json"
    adv_report = {row['area']: row for row in json.loads(adv_report_path.read_text())} if adv_report_path.exists() else {}
    cards, records = [], []
    for area in areas:
        cards.append(f'<h2>{html.escape(area["label"])}</h2><div class="grid">')
        for product, title in PRODUCTS.items():
            rel = f'{area["name"]}/{product}'
            directory = a.batch / rel
            truth = json.loads((directory / "map.json").read_text())
            preview = truth.get("coverage", {}).get("preview", False)
            if preview:
                title += " · preview"
            manifest = json.loads((directory / "manifest.json").read_text())
            reference = directory / "reference.json"
            record = dict(area=area["name"], label=area["label"], product=product, title=title, path=rel,
                          reference=reference.exists(), preview=preview, objects=len(truth["objects"]),
                          elements=len(truth["elements"]), bbox=area["bbox"],
                          attribution=manifest.get("attribution") or "Official ATKIS provider; see snapshot manifest")
            record['sourceNotice'] = ''
            if product == 'dtk50':
                transport = truth.get('source_snapshot', {}).get('transport')
                record['sourceNotice'] = 'Source: Linked AdV wrapper' if transport == 'adv-wrapper' else 'Source: previous direct-WFS snapshot'
                if transport != 'adv-wrapper' and area['name'] in adv_report:
                    record['sourceNotice'] += ' — ADV update unavailable; this map has not been migrated.'
            if reference.exists():
                record["referenceAttribution"] = json.loads(reference.read_text())["attribution"]
            records.append(record)
            cards.append(f'<article><h3>{title}</h3><button class="thumb" onclick="selectMap(\'{area["name"]}\',\'{product}\')">'
                         f'<img loading="lazy" src="{rel}/map.png" alt="{html.escape(area["label"])} {title}"></button>'
                         f'<p>{record["objects"]:,} visible objects · {record["elements"]:,} elements</p>'
                         f'<p><a href="{rel}/map.png">PNG</a> · <a href="{rel}/map.json">Ground truth</a> · '
                         f'<a href="{rel}/map.ttl">Knowledge graph</a> · <a href="{rel}/manifest.json">Manifest</a></p>'
                         f'<p>{html.escape(record["sourceNotice"])}</p>'
                         f'<small>{html.escape(record["attribution"])}</small></article>')
        cards.append('</div>')
    data = json.dumps(records, ensure_ascii=False).replace('<', '\\u003c')
    options = ''.join(f'<option value="{r["name"]}">{html.escape(r["label"])}</option>' for r in areas)
    products = ''.join(f'<option value="{key}">{title}</option>' for key, title in PRODUCTS.items())
    template = r'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Berlin map comparison · __AREA_COUNT__ areas</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f4f5f2;color:#182c29;font:16px system-ui,sans-serif}
main{max-width:1450px;margin:auto;padding:28px}h1{font-size:clamp(28px,4vw,48px);margin:8px 0}
p{line-height:1.55}a{color:#086d66}header>p{max-width:950px}.badge{font-size:13px;text-transform:uppercase;letter-spacing:.12em}
.notice{background:#fff3d6;padding:14px 18px;border-left:4px solid #b77a06}.controls{display:flex;gap:16px;flex-wrap:wrap;margin:20px 0}
label{display:grid;gap:6px}select,button{font:inherit;padding:9px;border:1px solid #afbab6;border-radius:5px;background:white;color:inherit}
.compare{display:grid;grid-template-columns:minmax(300px,640px) minmax(240px,1fr);gap:28px;background:white;padding:20px;border-radius:10px}
.frame{position:relative;width:100%;aspect-ratio:1;overflow:hidden}.frame img{position:absolute;width:100%;height:100%;image-rendering:auto}
#front{clip-path:inset(0 50% 0 0)}#seam{position:absolute;left:50%;height:100%;border-left:2px solid #00b7a5;pointer-events:none}
.captions{display:flex;justify-content:space-between;font-size:14px;margin-bottom:8px}.credit{font-size:12px;margin:6px 0}
input[type=range]{width:100%;accent-color:#087e74}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:18px}
article{background:white;border-radius:8px;padding:14px}article h3{font-size:17px;margin:0 0 12px}article p{font-size:13px}
.thumb{display:block;padding:0;border:0;width:100%;cursor:pointer}.thumb img{display:block;width:100%}small{display:block;font-size:11px;color:#4c625b}
pre{white-space:pre-wrap;word-break:break-word;font-size:13px}h2{margin-top:38px}
@media(max-width:900px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.compare{grid-template-columns:1fr}}
@media(max-width:500px){main{padding:14px}.grid{grid-template-columns:1fr}}
</style><main><header><div class="badge">Berlin & Brandenburg · first batch</div>
<h1>__AREA_COUNT__ areas. Four map products.</h1><p>__MAP_COUNT__ rendered maps, each paired with ground-truth JSON and a CartoGraph knowledge graph.
The __REFERENCE_COUNT__ OSM service references cover the same pixel grids and extents as their local renders.
Slide the comparison boundary to inspect alignment and cartographic differences.</p>
<p class="notice" __PREVIEW_HIDDEN__><strong>__PREVIEW_COUNT__ DTK50 outputs are previews.</strong> They use the DTK50 catalog with official Basis-DLM snapshots.
Missing source classes, attributes and presentation information are recorded in each coverage report. These are not complete official DTK50 maps.</p>
<p>The knowledge graphs describe our rendered images. Service references are comparison images and have no extracted knowledge graph.
Source dates, service style revisions, terrain and label placement can differ.</p>
__ADV_NOTE__
<p><a href="README.md">Run notes</a> · <a href="areas.json">Shared extents</a> · <a href="dataset.jsonl">Dataset index</a> · <a href="validation.json">Validation results</a></p></header>
<div class="controls"><label>Area<select id="area">__AREAS__</select></label><label>Product<select id="product">__PRODUCTS__</select></label></div>
<section class="compare" id="comparison"><div><div class="captions"><strong>Local render</strong><strong id="refCaption">Official service reference</strong></div>
<div class="frame"><img id="back" alt="Official service reference"><img id="front" alt="Local render"><div id="seam"></div></div>
<label id="sliderLabel">Compare render and reference<input id="slider" type="range" min="0" max="100" value="50"></label>
<p class="credit" id="attribution"></p><p class="credit"><a href="https://www.openstreetmap.org/copyright">© OpenStreetMap contributors</a></p></div>
<div><h2 id="selectedTitle" style="margin-top:0"></h2><p id="stats"></p><p id="links"></p><p id="note"></p><pre id="bbox"></pre></div></section>
__CARDS__
<footer><p>Reference tiles are cached with request metadata and checksums. Crops use integer pixel edges; no resampling is applied.
The reference downloader is limited to these five review views. Arrange appropriate service access before scaling reference acquisition to 100 maps.</p></footer></main>
<script>
const records=__DATA__, area=document.getElementById('area'), product=document.getElementById('product'), slider=document.getElementById('slider');
function update(){
 const r=records.find(x=>x.area===area.value&&x.product===product.value);
 document.getElementById('front').src=r.path+'/map.png';
 const back=document.getElementById('back');back.src=r.path+(r.reference?'/reference.png':'/map.png');
 document.getElementById('sliderLabel').hidden=!r.reference;
 document.getElementById('seam').hidden=!r.reference;
 document.getElementById('refCaption').textContent=r.reference?'Official service reference':r.title;
 document.getElementById('selectedTitle').textContent=r.label+' · '+r.title;
 document.getElementById('stats').textContent=r.objects.toLocaleString()+' visible objects · '+r.elements.toLocaleString()+' displayed elements. '+r.sourceNotice;
 const links=[['map.png','Rendered PNG'],['map.json','Ground-truth JSON'],['map.ttl','Knowledge graph (Turtle)'],['manifest.json','Manifest'],['validation.json','Validation']];
 if(r.reference)links.push(['reference.png','Reference PNG'],['reference.json','Reference provenance']);
 if(r.product==='dtk50')links.push(['coverage-report.json','DTK50 coverage report']);
 document.getElementById('links').innerHTML=links.map(([file,title])=>'<a href="'+r.path+'/'+file+'">'+title+'</a>').join('<br>');
 document.getElementById('note').textContent=r.reference?'Identical geographic extent, projection and pixel dimensions. Compare shapes and positions; labels may differ across tile/metatile boundaries.':(r.preview?'Incomplete DTK50-style preview. Consult the coverage report before using this map as training or evaluation data.':'No service reference downloaded for this product.');
 document.getElementById('bbox').textContent='WGS84 bounds (west, south, east, north)\n'+r.bbox.map(x=>x.toFixed(7)).join(', ');
 document.getElementById('attribution').textContent=r.attribution+(r.reference?' | Reference: '+r.referenceAttribution:'');
 slide();
}
function slide(){const r=records.find(x=>x.area===area.value&&x.product===product.value);const v=r.reference?slider.value:100;
 document.getElementById('front').style.clipPath='inset(0 '+(100-v)+'% 0 0)';document.getElementById('seam').style.left=v+'%';}
function selectMap(a,p){area.value=a;product.value=p;update();document.getElementById('comparison').scrollIntoView({behavior:'smooth'});}
area.addEventListener('change',update);product.addEventListener('change',update);slider.addEventListener('input',slide);update();
</script></html>'''
    preview_count = sum(row["preview"] for row in records)
    pending = [row['area'] for row in adv_report.values() if row['status'] == 'failed']
    adv_note = ('<p class="notice"><strong>ADV source update is partial.</strong> The wrapper currently cannot serve '
                + html.escape(', '.join(pending)) + '. Their previous WFS previews remain labelled as such. '
                '<a href="adv-snapshots/acquisition-report.json">ADV acquisition report</a></p>') if pending else ''
    replacements = {'__AREAS__': options, '__PRODUCTS__': products, '__CARDS__': ''.join(cards), '__DATA__': data,
                    '__AREA_COUNT__': str(len(areas)), '__MAP_COUNT__': str(len(records)),
                    '__REFERENCE_COUNT__': str(sum(row['reference'] for row in records)),
                    '__PREVIEW_COUNT__': str(preview_count), '__PREVIEW_HIDDEN__': '' if preview_count else 'hidden',
                    '__ADV_NOTE__': adv_note}
    for key, value in replacements.items():
        template = template.replace(key, value)
    (a.batch / "index.html").write_text(template, encoding="utf-8")
    print(a.batch / "index.html")


if __name__ == "__main__":
    main()
