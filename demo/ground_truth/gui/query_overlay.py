#!/usr/bin/env python3
"""Run a SPARQL query against a map's ground truth and see the answer on the map.

    python3 query_overlay.py                          # then pick a map
    python3 query_overlay.py Wien-z17.png             # the .ttl beside it is used
    python3 query_overlay.py --dataset demo/ground_truth/osm-carto/dataset

Type a query on the left, press Ctrl+Return, and every result that has a shape
is drawn over the image. Click a row to isolate it.

Anything a row mentions gets drawn: a value that is a WKT literal is drawn
directly, and a value that is a URI is followed to its cg:pixelGeometry - so
`SELECT ?o WHERE { ?o cg:visibleLabel ?l }` needs no geometry in the SELECT to
light up on the map.

If tkinter cannot show images - some Python builds ship a Tcl/Tk that aborts the
X connection on any PhotoImage - use the browser instead, which needs no GUI
toolkit at all:

    python3 query_overlay.py --web --dataset demo/ground_truth/osm-carto/dataset

There is also a windowless mode, handy for scripting and for checking a query
without opening anything:

    python3 query_overlay.py Wien-z17.png --run "SELECT ?o WHERE { ?o cg:visibleLabel ?l }" \
        --screenshot out.png

Only rdflib and Pillow are needed beyond the standard library.
"""

import argparse
import base64
import colorsys
import io
import os
import sys
import time
import tkinter as tk
from tkinter import filedialog, ttk

try:
    from PIL import Image, ImageDraw
    from rdflib import Graph, URIRef
    from rdflib.term import Literal
except ImportError:
    sys.exit("this tool needs rdflib and Pillow: pip install rdflib Pillow")


def to_tk_image(image):
    """A tkinter.PhotoImage from a PIL image, without PIL.ImageTk.

    ImageTk needs Pillow's _imagingtk to have been built against the very Tcl/Tk
    the interpreter loads; a mismatched wheel fails with
    "TypeError: bad argument type for built-in operation" and then aborts the X
    connection. Tk 8.6 decodes PNG by itself, so handing it encoded bytes avoids
    that dependency entirely.
    """
    buffer = io.BytesIO()
    image.convert("RGBA").save(buffer, format="PNG")
    return tk.PhotoImage(data=base64.b64encode(buffer.getvalue()))

CG = "https://w3id.org/cartograph#"
CGP = "https://w3id.org/cartograph-provenance#"

PRESETS = {
    "everything with a readable label": """
SELECT ?object ?label WHERE {
  ?object cg:visibleLabel ?label .
}""",
    "objects by dimension (0D points)": """
SELECT ?object ?layer WHERE {
  ?object cg:hasDisplayedElement ?e ; cg:inLayer [ cg:layerName ?layer ] .
  ?e cg:anchorDimension 0 .
}""",
    "1D lines": """
SELECT ?object ?layer WHERE {
  ?object cg:hasDisplayedElement ?e ; cg:inLayer [ cg:layerName ?layer ] .
  ?e cg:anchorDimension 1 .
}""",
    "2D areas": """
SELECT ?object ?layer WHERE {
  ?object cg:hasDisplayedElement ?e ; cg:inLayer [ cg:layerName ?layer ] .
  ?e cg:anchorDimension 2 .
}""",
    "the ten biggest visible objects": """
SELECT ?object ?layer ?pixels WHERE {
  ?object cg:visiblePixelCount ?pixels ; cg:inLayer [ cg:layerName ?layer ] .
}
ORDER BY DESC(?pixels)
LIMIT 10""",
    "objects cut off by the viewport": """
SELECT ?object ?layer WHERE {
  ?e cg:isClipped true ; cg:partOfVisibleObject ?object ; cg:inLayer [ cg:layerName ?layer ] .
}""",
    "drawn but completely covered (needs --candidates)": """
SELECT ?candidate ?layer WHERE {
  ?candidate cg:visibilityStatus cg:Occluded ; cg:inLayer [ cg:layerName ?layer ] .
}""",
    "OBJECTS - one row per feature in a stylesheet layer": """
SELECT ?object ?layer ?label ?pixels WHERE {
  ?object a cg:VisibleObject ;
          cg:inLayer [ cg:layerName ?layer ] ;
          cg:visiblePixelCount ?pixels .
  OPTIONAL { ?object cg:visibleLabel ?label }
}
ORDER BY DESC(?pixels)""",
    "ELEMENTS grouped by their object": """
SELECT ?object ?element ?layer ?dimension ?pixels WHERE {
  ?element cg:partOfVisibleObject ?object ;
           cg:anchorDimension ?dimension ;
           cg:pixelCount ?pixels ;
           cg:inLayer [ cg:layerName ?layer ] .
}
ORDER BY ?object ?element""",
    "ELEMENTS grouped by OSM way - across stylesheet layers": """
SELECT ?osm_id ?layer ?element ?dimension ?pixels WHERE {
  ?element cg:partOfVisibleObject ?object ;
           cg:anchorDimension ?dimension ;
           cg:pixelCount ?pixels ;
           cg:inLayer [ cg:layerName ?layer ] .
  ?object cg:representsFeature ?feature .
  ?feature ex:osm_id ?osm_id .
}
ORDER BY ?osm_id ?element""",
    "one OSM way drawn as several objects (casing, fill, label)": """
SELECT ?osm_id ?layer ?object ?elements WHERE {
  {
    SELECT ?osm_id (COUNT(DISTINCT ?o) AS ?objects) WHERE {
      ?o a cg:VisibleObject ; cg:representsFeature [ ex:osm_id ?osm_id ] .
    } GROUP BY ?osm_id HAVING(COUNT(DISTINCT ?o) > 1)
  }
  ?object a cg:VisibleObject ; cg:representsFeature [ ex:osm_id ?osm_id ] ;
          cg:inLayer [ cg:layerName ?layer ] .
  { SELECT ?object (COUNT(?e) AS ?elements) WHERE { ?e cg:partOfVisibleObject ?object } GROUP BY ?object }
}
ORDER BY ?osm_id""",
    "elements grouped by the object they belong to": """
SELECT ?object ?element ?layer ?dimension ?pixels WHERE {
  ?element cg:partOfVisibleObject ?object ;
           cg:anchorDimension ?dimension ;
           cg:pixelCount ?pixels ;
           cg:inLayer [ cg:layerName ?layer ] .
}
ORDER BY ?object ?element""",
    "one road: its casing, fill and label together": """
SELECT ?object ?element ?layer ?dimension WHERE {
  ?object cg:visibleLabel ?label ; cg:hasDisplayedElement ?element .
  ?element cg:anchorDimension ?dimension ; cg:inLayer [ cg:layerName ?layer ] .
}
ORDER BY ?object""",
    "marker symbols by icon": """
SELECT ?object ?icon WHERE {
  ?e cg:hasPortrayal [ a cg:MarkerPortrayal ; cg:iconName ?icon ] ;
     cg:partOfVisibleObject ?object .
}""",
}

PREFIXES = """PREFIX cg: <https://w3id.org/cartograph#>
PREFIX cgp: <https://w3id.org/cartograph-provenance#>
PREFIX geo: <http://www.opengis.net/ont/geosparql#>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
"""


def prefixes_for(graph):
    """The standard prefixes, plus `ex:` bound to this graph's own namespace.

    Every map mints its individuals under a different base (…/Wien/z17/), so a
    query that wants ex:osm_id can only be written once if the prefix is filled
    in per graph.
    """
    if graph is not None:
        for prefix, uri in graph.namespaces():
            if prefix == "ex":
                return PREFIXES + "PREFIX ex: <{}>\n".format(uri)
    return PREFIXES


def parse_multipolygon(text):
    """MULTIPOLYGON (((x y, ...), ...), ...) -> list of rings."""
    if "MULTIPOLYGON" not in text.upper():
        return []
    inner = text[text.index("(") + 1: text.rindex(")")]
    rings, depth, current = [], 0, []
    for ch in inner:
        if ch == "(":
            depth += 1
            if depth == 2:
                current = []
                continue
        elif ch == ")":
            if depth == 2:
                points = []
                for pair in "".join(current).split(","):
                    parts = pair.split()
                    if len(parts) == 2:
                        points.append((float(parts[0]), float(parts[1])))
                if len(points) >= 3:
                    rings.append(points)
            depth -= 1
            continue
        if depth >= 2:
            current.append(ch)
    return rings


class Overlay(tk.Tk):
    def __init__(self, image_path=None, dataset=None):
        super().__init__()
        self.title("Visual ground truth - query overlay")
        self.geometry("1400x820")
        self.graph = None
        self.image = None
        self.image_path = None
        self.rings_per_row = []
        self.full_per_row = []
        self.group_per_row = []
        self.selected = None
        self.dataset = dataset

        self._build()
        if image_path:
            self.load(image_path)
        elif dataset:
            self._fill_dataset(dataset)

    # ---------------------------------------------------------------- layout
    def _build(self):
        bar = ttk.Frame(self, padding=6)
        bar.pack(fill=tk.X)
        ttk.Button(bar, text="Open map…", command=self.choose).pack(side=tk.LEFT)
        self.place_box = ttk.Combobox(bar, width=34, state="readonly")
        self.place_box.pack(side=tk.LEFT, padx=6)
        self.place_box.bind("<<ComboboxSelected>>", lambda _: self.load(self.place_box.get()))
        self.status = ttk.Label(bar, text="no map loaded")
        self.status.pack(side=tk.LEFT, padx=12)

        ttk.Label(bar, text="fill").pack(side=tk.LEFT)
        self.alpha = tk.IntVar(value=90)
        ttk.Scale(bar, from_=0, to=255, variable=self.alpha, length=110,
                  command=lambda _: self.redraw()).pack(side=tk.LEFT)
        self.only_selected = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="selected row only", variable=self.only_selected,
                        command=self.redraw).pack(side=tk.LEFT, padx=8)
        self.group_colour = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="colour by first column", variable=self.group_colour,
                        command=self.redraw).pack(side=tk.LEFT)

        panes = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        panes.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(panes, padding=4)
        panes.add(left, weight=1)
        self.preset = ttk.Combobox(left, values=sorted(PRESETS), state="readonly")
        self.preset.pack(fill=tk.X)
        self.preset.bind("<<ComboboxSelected>>", self._use_preset)
        self.query = tk.Text(left, height=13, wrap=tk.NONE, font=("monospace", 10),
                             undo=True)
        self.query.pack(fill=tk.X, pady=4)
        self.query.insert("1.0", PRESETS["everything with a readable label"].strip())
        run = ttk.Frame(left)
        run.pack(fill=tk.X)
        ttk.Button(run, text="Run  (Ctrl+Return)", command=self.run).pack(side=tk.LEFT)
        self.result_label = ttk.Label(run, text="")
        self.result_label.pack(side=tk.LEFT, padx=8)

        self.table = ttk.Treeview(left, show="headings", selectmode="browse")
        self.table.pack(fill=tk.BOTH, expand=True, pady=4)
        self.table.bind("<<TreeviewSelect>>", self._select_row)
        scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)

        right = ttk.Frame(panes)
        panes.add(right, weight=2)
        self.canvas = tk.Label(right, background="#202020")
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.bind("<Control-Return>", lambda _: self.run())

    def _use_preset(self, _):
        self.query.delete("1.0", tk.END)
        self.query.insert("1.0", PRESETS[self.preset.get()].strip())

    def _fill_dataset(self, root):
        maps = sorted(os.path.join(base, name)
                      for base, _, names in os.walk(root)
                      for name in names if name.endswith(".png"))
        self.place_box["values"] = maps
        # Deliberately not loading one: the first entry of a full dataset is a
        # z13 graph of several hundred thousand triples, and parsing it at
        # startup would look like a hang.
        self.status.configure(text="{} maps found - pick one".format(len(maps)))

    # ------------------------------------------------------------------ data
    def choose(self):
        path = filedialog.askopenfilename(filetypes=[("map image", "*.png")])
        if path:
            self.load(path)

    def load(self, image_path):
        turtle = os.path.splitext(image_path)[0] + ".ttl"
        if not os.path.exists(turtle):
            self.status.configure(text="no .ttl beside {}".format(os.path.basename(image_path)))
            return
        self.image_path = image_path
        self.image = Image.open(image_path).convert("RGBA")
        self.show(self.image)
        # Parsed on the main thread on purpose. Tk is not thread-safe, and
        # doing this in a worker aborts the X11 connection with
        # "Unknown sequence number while appending request". A big z13 graph
        # takes a few seconds and the window is unresponsive meanwhile; that is
        # the honest trade for not crashing.
        size = os.path.getsize(turtle) / 1e6
        self.status.configure(text="parsing {}  ({:.1f} MB) …".format(os.path.basename(turtle), size))
        self.configure(cursor="watch")
        self.update_idletasks()
        started = time.time()
        try:
            graph = Graph()
            graph.parse(turtle)
        except Exception as error:
            self.graph = None
            self.status.configure(text="could not parse {}: {}".format(
                os.path.basename(turtle), str(error)[:60]))
            self.configure(cursor="")
            return
        self.graph = graph
        self.status.configure(text="{}  ·  {:,} triples  ·  {:.1f} MB  ·  {:.1f}s".format(
            os.path.basename(turtle), len(graph), size, time.time() - started))
        self.configure(cursor="")

    # ----------------------------------------------------------------- query
    def run(self):
        if self.graph is None:
            self.result_label.configure(text="still loading the graph…")
            return "break"
        text = self.query.get("1.0", tk.END).strip()
        started = time.time()
        try:
            rows = list(self.graph.query(prefixes_for(self.graph) + text))
        except Exception as error:
            self.result_label.configure(text="query error: {}".format(str(error)[:70]))
            return "break"

        names = [str(v) for v in (rows[0].labels if rows else [])]
        self.table.delete(*self.table.get_children())
        self.table["columns"] = names or ["result"]
        for name in names or ["result"]:
            self.table.heading(name, text=name)
            self.table.column(name, width=140, stretch=True)

        base = self._base_namespace()
        self.rings_per_row = []
        self.full_per_row = []
        self.group_per_row = []
        for row in rows:
            values = []
            rings = []
            for value in row:
                values.append(self._short(value, base))
                rings.extend(self._rings_for(value))
            self.full_per_row.append([self._full(v) for v in row])
            self.table.insert("", tk.END, values=values or ["-"])
            self.rings_per_row.append(rings)
            # Colour by the first column, so every row naming the same object
            # gets the same colour: that is what makes "these elements belong
            # together" visible at a glance.
            self.group_per_row.append(str(row[0]) if len(row) else str(len(self.rings_per_row)))

        drawn = sum(1 for r in self.rings_per_row if r)
        self.result_label.configure(text="{} row(s), {} with a shape, {:.2f}s".format(
            len(rows), drawn, time.time() - started))
        self.selected = None
        self.redraw()
        return "break"

    def _short(self, value, base=None):
        """A readable form that still identifies the node.

        Keeping only the last path segment turned every URI into a bare number,
        which identifies nothing: the data namespace is dropped instead, so
        `.../visible-object/roads-fill/123` reads as `visible-object/roads-fill/123`.
        The untruncated value is always available too - see `_full`.
        """
        if value is None:
            return ""
        text = str(value)
        if isinstance(value, URIRef):
            for prefix in ((base,) if base else ()) + (CG, CGP):
                if prefix and text.startswith(prefix):
                    return text[len(prefix):]
            return text
        return text if len(text) < 80 else text[:77] + "…"

    @staticmethod
    def _full(value):
        return "" if value is None else str(value)

    def _base_namespace(self):
        """The namespace this graph minted its individuals in (bound as `ex`)."""
        if self.graph is None:
            return None
        for prefix, uri in self.graph.namespaces():
            if prefix == "ex":
                return str(uri)
        return None

    def _rings_for(self, value):
        """Shapes for one result value: a WKT literal, or a node that has one."""
        if value is None:
            return []
        if isinstance(value, Literal):
            return parse_multipolygon(str(value))
        rings = []
        for _, _, geometry in self.graph.triples((value, URIRef(CG + "pixelGeometry"), None)):
            rings.extend(parse_multipolygon(str(geometry)))
        if not rings:
            # a visible object may only carry geometry on its elements
            for _, _, element in self.graph.triples((value, URIRef(CG + "hasDisplayedElement"), None)):
                for _, _, geometry in self.graph.triples((element, URIRef(CG + "pixelGeometry"), None)):
                    rings.extend(parse_multipolygon(str(geometry)))
        return rings

    # ------------------------------------------------------------------ draw
    def redraw(self):
        if self.image is None:
            return
        overlay = Image.new("RGBA", self.image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay, "RGBA")
        alpha = int(self.alpha.get())
        rows = list(enumerate(self.rings_per_row))
        if self.only_selected.get() and self.selected is not None:
            rows = [(self.selected, self.rings_per_row[self.selected])]
        keys = []
        for key in self.group_per_row:
            if key not in keys:
                keys.append(key)
        for index, rings in rows:
            key = (self.group_per_row[index]
                   if self.group_colour.get() and index < len(self.group_per_row) else index)
            hue = ((keys.index(key) if key in keys else index) * 0.13) % 1.0
            r, g, b = colorsys.hsv_to_rgb(hue, 0.9, 1.0)
            colour = (int(r * 255), int(g * 255), int(b * 255))
            width = 3 if index == self.selected else 1
            for ring in rings:
                if len(ring) >= 3:
                    draw.polygon(ring, fill=colour + (alpha,), outline=colour + (255,))
                    if width > 1:
                        draw.line(ring + [ring[0]], fill=(255, 255, 255, 255), width=width)
        self.show(Image.alpha_composite(self.image, overlay))

    def _select_row(self, _):
        chosen = self.table.selection()
        if not chosen:
            return
        self.selected = self.table.index(chosen[0])
        if self.selected < len(self.full_per_row):
            self.status.configure(text="  |  ".join(self.full_per_row[self.selected]))
        self.redraw()

    def show(self, image):
        # keep a reference, or Tk garbage-collects the image and shows nothing
        self.photo = to_tk_image(image)
        self.canvas.configure(image=self.photo)


# ---------------------------------------------------------------- web mode
#
# A one-page local app, so the tool runs on any interpreter: some Python builds
# ship a Tcl/Tk that aborts the X connection on any image, which makes the
# tkinter window unusable through no fault of the tool.

PAGE = """<!doctype html>
<meta charset="utf-8"><title>ground truth - query overlay</title>
<style>
 body{margin:0;font:13px system-ui,sans-serif;display:flex;height:100vh}
 #left{width:520px;display:flex;flex-direction:column;border-right:1px solid #ccc}
 #right{flex:1;background:#202020;display:flex;align-items:center;justify-content:center;overflow:auto}
 textarea{font:12px monospace;height:190px;border:0;border-bottom:1px solid #ccc;padding:8px;resize:vertical}
 #bar{padding:6px;display:flex;gap:6px;align-items:center;flex-wrap:wrap;border-bottom:1px solid #ccc}
 table{border-collapse:collapse;width:100%;font-size:12px}
 th{position:sticky;top:0;background:#eee;text-align:left;padding:3px 6px}
 td{padding:2px 6px;border-top:1px solid #eee;cursor:pointer;white-space:nowrap;
    overflow:hidden;text-overflow:ellipsis;max-width:220px}
 tr.sel td{background:#fdf0a0}
 #rows{overflow:auto;flex:1}
 #stage{position:relative}
 svg{position:absolute;left:0;top:0}
 #status{padding:4px 8px;background:#f6f6f6;border-top:1px solid #ccc;font-size:12px}
</style>
<div id=left>
  <div id=bar>
    <select id=map></select>
    <select id=preset></select>
    <button onclick=run()>Run (Ctrl+Enter)</button>
    <label>fill <input id=alpha type=range min=0 max=100 value=35 oninput=draw()></label>
    <label><input id=only type=checkbox onchange=draw()> selected only</label>
    <label><input id=grp type=checkbox checked onchange=draw()> colour by first column</label>
  </div>
  <textarea id=q>SELECT ?object ?label WHERE { ?object cg:visibleLabel ?label }</textarea>
  <div id=rows></div>
  <div id=status>pick a map</div>
</div>
<div id=right><div id=stage><img id=img><svg id=svg></svg></div></div>
<script>
let shapes=[], full=[], groups=[], sel=-1, W=0, H=0;
const $=id=>document.getElementById(id);
fetch('maps').then(r=>r.json()).then(m=>{
  $('map').innerHTML=m.map(p=>`<option>${p}</option>`).join('');
  if(m.length) load();
});
fetch('presets').then(r=>r.json()).then(p=>{
  $('preset').innerHTML='<option value="">presets…</option>'+Object.keys(p).map(k=>`<option>${k}</option>`).join('');
  $('preset').onchange=()=>{ if($('preset').value) $('q').value=p[$('preset').value].trim(); };
});
$('map').onchange=load;
function load(){
  const p=$('map').value;
  $('img').src='image?path='+encodeURIComponent(p);
  $('img').onload=()=>{ W=$('img').naturalWidth; H=$('img').naturalHeight;
    $('svg').setAttribute('width',W); $('svg').setAttribute('height',H); shapes=[]; draw(); };
  $('status').textContent='loading '+p+' …';
  fetch('load?path='+encodeURIComponent(p)).then(r=>r.json())
    .then(d=>$('status').textContent=d.status);
}
function run(){
  $('status').textContent='querying…';
  fetch('query',{method:'POST',body:JSON.stringify({path:$('map').value,query:$('q').value})})
   .then(r=>r.json()).then(d=>{
     if(d.error){ $('status').textContent=d.error; return; }
     $('rows').innerHTML='<table><tr>'+d.columns.map(c=>`<th>${c}</th>`).join('')+'</tr>'+
       d.rows.map((r,i)=>`<tr onclick=pick(${i})>`+
         r.map((v,j)=>`<td title="${(d.full&&d.full[i]&&d.full[i][j])||v}">${v}</td>`).join('')+'</tr>').join('')+'</table>';
     full=d.full||[]; groups=d.groups||[];
     shapes=d.shapes; sel=-1; $('status').textContent=d.status; draw();
   });
}
function pick(i){ sel=i;
  [...document.querySelectorAll('#rows tr')].forEach((tr,k)=>tr.classList.toggle('sel',k===i+1));
  if(full[i]) $('status').textContent=full[i].join('   |   ');
  draw(); }
function draw(){
  const a=$('alpha').value/100, only=$('only').checked, byGroup=$('grp').checked;
  // colour by the first column, so all rows naming one object share a colour
  const keys=[]; groups.forEach(g=>{ if(!keys.includes(g)) keys.push(g); });
  let out='';
  shapes.forEach((rings,i)=>{
    if(only && sel>=0 && i!==sel) return;
    const k=(byGroup && groups.length)?keys.indexOf(groups[i]):i;
    const c=`hsl(${(k*47)%360} 90% 55%)`;
    const w=(i===sel)?3:1;
    rings.forEach(r=>{ out+=`<polygon points="${r.map(p=>p[0]+','+p[1]).join(' ')}" fill="${c}" fill-opacity="${a}" stroke="${c}" stroke-width="${w}"/>`; });
  });
  $('svg').innerHTML=out;
}
document.addEventListener('keydown',e=>{ if(e.ctrlKey&&e.key==='Enter') run(); });
</script>
"""


def serve(dataset, port, root):
    import http.server
    import json as jsonlib
    import urllib.parse

    root = os.path.abspath(root)
    state = {"path": None, "graph": None}

    def maps():
        if not dataset:
            return []
        return sorted(os.path.relpath(os.path.join(base, name), root)
                      for base, _, names in os.walk(dataset)
                      for name in names if name.endswith(".png"))

    def resolve(relative):
        """Refuse anything outside the served root: this is a local tool, but a
        path from a request is still a path from outside."""
        full = os.path.abspath(os.path.join(root, relative))
        if not full.startswith(root + os.sep):
            raise ValueError("path outside the served directory")
        return full

    def ensure(relative):
        full = resolve(relative)
        if state["path"] != full:
            turtle = os.path.splitext(full)[0] + ".ttl"
            started = time.time()
            graph = Graph()
            graph.parse(turtle)
            state.update(path=full, graph=graph,
                         status="{}  ·  {:,} triples  ·  {:.1f}s".format(
                             os.path.basename(turtle), len(graph), time.time() - started))
        return state

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body, kind="application/json"):
            payload = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            try:
                if parsed.path == "/":
                    return self.send(200, PAGE, "text/html; charset=utf-8")
                if parsed.path == "/maps":
                    return self.send(200, jsonlib.dumps(maps()))
                if parsed.path == "/presets":
                    return self.send(200, jsonlib.dumps(PRESETS))
                if parsed.path == "/image":
                    with open(resolve(query["path"][0]), "rb") as handle:
                        return self.send(200, handle.read(), "image/png")
                if parsed.path == "/load":
                    return self.send(200, jsonlib.dumps({"status": ensure(query["path"][0])["status"]}))
            except Exception as error:
                return self.send(400, jsonlib.dumps({"error": str(error)[:200]}))
            self.send(404, jsonlib.dumps({"error": "not found"}))

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            request = jsonlib.loads(self.rfile.read(length) or b"{}")
            try:
                current = ensure(request["path"])
                graph = current["graph"]
                started = time.time()
                rows = list(graph.query(prefixes_for(graph) + request["query"]))
                lookup = type("L", (), {"graph": graph, "_rings_for": Overlay._rings_for})()
                columns = [str(v) for v in (rows[0].labels if rows else [])] or ["result"]
                base = None
                for prefix, uri in graph.namespaces():
                    if prefix == "ex":
                        base = str(uri)
                table, full, shapes, groups = [], [], [], []
                for row in rows:
                    table.append([Overlay._short(None, v, base) for v in row])
                    full.append([Overlay._full(v) for v in row])
                    groups.append(str(row[0]) if len(row) else "")
                    rings = []
                    for value in row:
                        rings.extend(lookup._rings_for(value))
                    shapes.append([[[round(x, 1), round(y, 1)] for x, y in ring] for ring in rings])
                with_shape = sum(1 for s in shapes if s)
                return self.send(200, jsonlib.dumps(dict(
                    columns=columns, rows=table, full=full, shapes=shapes, groups=groups,
                    status="{} row(s), {} with a shape, {:.2f}s".format(
                        len(rows), with_shape, time.time() - started))))
            except Exception as error:
                return self.send(400, jsonlib.dumps({"error": str(error)[:200]}))

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("open http://127.0.0.1:{}/   (serving {}, Ctrl+C to stop)".format(port, root))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()


def headless(image_path, query, out_path, alpha):
    """Run one query and write the overlay, without opening a window."""
    turtle = os.path.splitext(image_path)[0] + ".ttl"
    graph = Graph()
    graph.parse(turtle)
    image = Image.open(image_path).convert("RGBA")

    if query in PRESETS:
        query = PRESETS[query]
    rows = list(graph.query(prefixes_for(graph) + query))

    lookup = type("L", (), {"graph": graph, "_rings_for": Overlay._rings_for})()
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    with_shape = 0
    for index, row in enumerate(rows):
        rings = []
        for value in row:
            rings.extend(lookup._rings_for(value))
        if rings:
            with_shape += 1
        red, green, blue = colorsys.hsv_to_rgb((index * 0.13) % 1.0, 0.9, 1.0)
        colour = (int(red * 255), int(green * 255), int(blue * 255))
        for ring in rings:
            if len(ring) >= 3:
                draw.polygon(ring, fill=colour + (alpha,), outline=colour + (255,))
    Image.alpha_composite(image, overlay).save(out_path)
    print("{}: {} row(s), {} with a shape -> {}".format(
        os.path.basename(image_path), len(rows), with_shape, out_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", nargs="?", help="a map .png with a .ttl beside it")
    parser.add_argument("--dataset", help="directory to browse for maps")
    parser.add_argument("--run", help="run this query (or preset name) instead of opening a window")
    parser.add_argument("--screenshot", help="where to write the overlay in --run mode")
    parser.add_argument("--alpha", type=int, default=90, help="fill opacity, 0-255")
    parser.add_argument("--list-presets", action="store_true")
    parser.add_argument("--web", action="store_true",
                        help="serve a browser UI instead of opening a window (no GUI toolkit needed)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--root", default=".", help="directory the web mode is allowed to serve from")
    opts = parser.parse_args()

    if opts.list_presets:
        for name in sorted(PRESETS):
            print(" ", name)
        return
    if opts.web:
        serve(opts.dataset, opts.port, opts.root)
        return
    if opts.run:
        if not opts.image:
            sys.exit("--run needs a map image")
        headless(opts.image, opts.run, opts.screenshot or "overlay.png", opts.alpha)
        return
    Overlay(opts.image, opts.dataset).mainloop()


if __name__ == "__main__":
    main()
