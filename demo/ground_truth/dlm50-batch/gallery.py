"""Create a live, locally served gallery without exposing raw snapshot caches."""
from pathlib import Path
import json

HTML='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>100 DTK50 maps and knowledge graphs</title>
<style>body{font:16px/1.5 system-ui;background:#f2f4f7;color:#182430;margin:24px}main{max-width:1600px;margin:auto}h1{margin-bottom:8px}.notice{background:#fff0ca;padding:14px;border-radius:8px}nav{display:flex;gap:8px;flex-wrap:wrap;margin:20px 0}button,input{font:inherit;padding:8px 12px;border:1px solid #aab5c0;border-radius:6px;background:white}button.active{background:#124968;color:white}a{color:#075b87}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px}.card{background:white;padding:16px;border-radius:10px}.card h2{font-size:19px;margin:0 0 8px}.card img{width:100%;aspect-ratio:1;object-fit:contain;background:#eef0f2}.links{display:flex;gap:12px;flex-wrap:wrap}.state{color:#526779;font-size:14px}.pending{display:grid;place-items:center;aspect-ratio:1;background:#eef0f2;color:#526779}.error{color:#a22525}#summary{font-weight:600}progress{width:100%;height:16px}footer{margin-top:28px;font-size:14px}</style>
<main><h1>100 DTK50 maps and knowledge graphs</h1>
<p>25 views each from NRW, Saxony, Hesse and Rhineland-Palatinate. Source: <a href="https://wunderfacts.com/adv/">Linked AdV DLM50</a>. Maps use the repaired gpkg-to-dtk50 pipeline and Mapnik styles at 1:50,000, 640 × 640 pixels.</p>
<p class="notice">Generated previews, not official DTK50 rasters. Coverage gaps and remaining cartographic differences are reported with each map. A validated graph confirms structural and image consistency, not full cartographic fidelity. Terrain classes are approximate area-level assumptions.</p>
<p id="summary">Loading progress…</p><progress id="bar" max="100" value="0"></progress><p id="updated"></p>
<nav id="filters"><button class="active" data-state="All">All states</button><button data-state="NRW">NRW</button><button data-state="Saxony">Saxony</button><button data-state="Hesse">Hesse</button><button data-state="Rhineland-Palatinate">Rhineland-Palatinate</button><input id="search" type="search" placeholder="Find an area" aria-label="Find an area"></nav>
<p id="downloads"></p><div id="cards" class="grid"></div>
<footer><a href="areas.json">All map extents and terrain assumptions</a> · <a href="progress.json">Machine-readable progress</a> · <a href="README.md">Dataset notes</a><p>Attribution: official state DLM50 data via Linked AdV; source object IRIs and provenance are retained in the graphs. Maps and graphs are paired by the dataset index. Graph image URLs retain their original local build paths.</p></footer></main>
<script>
let data=null, selected='All', last='';
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function display(){if(!data)return;const q=document.querySelector('#search').value.toLowerCase();
 const rows=data.areas.filter(a=>(selected==='All'||a.state===selected)&&a.label.toLowerCase().includes(q));
 const html=rows.map(a=>{let ready=a.status==='ready';let links=ready?`<p class="links"><a href="${a.name}/map.png">Map PNG</a><a href="${a.name}/map.ttl" download>Knowledge graph</a><a href="${a.name}/map.json" download>Ground truth</a><a href="${a.name}/coverage-report.json">Coverage</a><a href="${a.name}/validation.json">Validation</a></p>`:'';
 return `<article class="card"><h2>${esc(a.label)}</h2><div class="state">${esc(a.state)} · ${esc(a.status)}</div>${ready?`<a href="${a.name}/map.png"><img loading="lazy" src="${a.name}/map.png" alt="DTK50 ${esc(a.label)}"></a>`:`<div class="pending ${a.status==='failed'?'error':''}">${esc(a.status==='failed'?'Processing failed; retry pending':a.status)}</div>`}${links}${ready?`<p>${a.validation.visible_objects.toLocaleString()} visible objects · ${a.validation.triples.toLocaleString()} triples · SHACL passed</p>`:''}</article>`}).join('');
 if(html!==last){document.querySelector('#cards').innerHTML=html;last=html;}
 document.querySelectorAll('button[data-state]').forEach(b=>{b.classList.toggle('active',b.dataset.state===selected);const state=b.dataset.state;const candidates=data.areas.filter(a=>state==='All'||a.state===state);b.textContent=(state==='All'?'All states':state)+' · '+candidates.filter(a=>a.status==='ready').length+'/'+candidates.length;});
}
async function refresh(){try{const r=await fetch('progress.json',{cache:'no-store'});data=await r.json();document.querySelector('#summary').textContent=`${data.ready} / 100 map–graph pairs ready · ${data.phase}`;document.querySelector('#bar').value=data.ready;document.querySelector('#updated').textContent='Updated '+new Date(data.updated_at).toLocaleString();document.querySelector('#downloads').innerHTML=(data.archives||[]).map(a=>`<a href="${a.file}" download>Download ${esc(a.state)} — 25 maps + graphs</a>`).join(' · ');display();}catch(e){document.querySelector('#updated').textContent='Progress temporarily unavailable; retrying.';}}
document.querySelector('#filters').addEventListener('click',e=>{if(e.target.dataset.state){selected=e.target.dataset.state;display();}});document.querySelector('#search').addEventListener('input',display);refresh();setInterval(refresh,30000);
</script></html>'''


def initialize(batch):
    public=batch/'public';public.mkdir(exist_ok=True)
    (public/'index.html').write_text(HTML)
    (public/'areas.json').write_bytes((batch/'areas.json').read_bytes())
    (public/'README.md').write_text('''# DTK50 maps from four ADV DLM50 dumps

100 distinct 640×640 views: 25 each in NRW, Saxony, Hesse and Rhineland-Palatinate.
The map scale denominator is 50,000. EPSG:3857 extents and the 250 m acquisition
buffer are checked against Eurostat GISCO NUTS 2024 state boundaries.

Sources are pinned to the appropriate ADV DLM50 dump, with state object-prefix
checks, cached/checksummed raw responses and verified road-parent retrieval.
Existing matching snapshots are reused; acquisition dates are retained in the
map metadata. Source datasets are not necessarily from the same survey date.

Every ready card has a map PNG, Mapnik ground-truth JSON, RDF/Turtle knowledge
graph, coverage report and validation report. Graphs pass CartoGraph SHACL and
map/extent/object-count/provenance checks. Graph image URLs retain local build
paths; use the gallery or dataset index to locate their hosted PNG counterparts.

These are generated previews, not official reference rasters. Completeness gaps,
missing input classes and remaining cartographic differences are not erased by
graph validation. Regional terrain classes are explicit approximate assumptions,
not per-feature surveyed classifications. Map centers favor varied settlements
and surrounding landscapes; this is not a statistically representative sample.

Attribution: official DLM50 of the respective state, accessed through Linked AdV
(https://wunderfacts.com/adv/). Original source identifiers remain in the graphs.
State-boundary verification: Eurostat GISCO NUTS 2024, 1:1M.
''')
