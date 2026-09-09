# Visual ground truth demos

Three pieces, all driven by `mapnik::visual_ground_truth_collector`, which
collects the machine-readable ground truth in the *same* AGG pass that produces
the image.

| | |
|---|---|
| `visual_ground_truth_demo.cpp` | `mapnik-ground-truth-demo` — a synthetic map built in code, rigged so every extraction rule is visible by eye |
| `visual_ground_truth_render.cpp` | `mapnik-ground-truth-render` — renders **any** Mapnik XML stylesheet and writes `<prefix>.png` + `<prefix>.json` |
| `annotate.py` | draws the JSON back onto the image it came from (Pillow) |
| `mapnik_ground_truth_c.cpp` | `libmapnik-ground-truth-c.so` — a flat C entry point for FFI consumers |
| `mapnik_ground_truth.py` | Python API over that library, via ctypes — no subprocess, no third-party package |
| `rdf/ground_truth_to_rdf.py` | ground-truth JSON → CartoGraph RDF (`cg:` / `cgp:`) |
| `rdf/validate_and_query.py` | SHACL validation + the demo SPARQL queries |

## Synthetic demo

```sh
./build/out/mapnik-ground-truth-demo demo/ground_truth/example
python3 demo/ground_truth/annotate.py \
    demo/ground_truth/example.png demo/ground_truth/example.json \
    demo/ground_truth/example.annotated.png
```

The scene contains a reef that is completely covered, an island outside the
viewport, a road drawn as casing plus fill, and a city whose marker *and* label
are buried by a later polygon. None of the first three appear in the output; the
road appears once, with two displayed elements.

## Any stylesheet

For classic **OpenTopoMap** and **Humanitarian OSM**, see
[`map-products/README.md`](map-products/README.md). The workflow produces a PNG,
ground-truth JSON and linked Turtle RDF for either product or both together.

```sh
./build/out/mapnik-ground-truth-render <map.xml> <prefix> \
    --size 1200 900 --extent-wgs84 minx miny maxx maxy \
    --plugins build/out/plugins/input --fonts fonts/dejavu-fonts-ttf-2.37/ttf \
    --identity osm_id --simplify 1.0 [--no-attributes]
```

`--identity NAME` reports an attribute as identity metadata instead of
source-only metadata. `--no-attributes` keeps datasource tags out of the result
entirely — on a dense OSM extract that is the difference between a 42 MB and a
3.5 MB JSON.

## OpenStreetMap

`osm/fetch_osm.py` pulls a small extract from Overpass and writes one GeoJSON
file per thematic layer, which `osm/osm.xml` styles in an OSM-Carto-like way.
No database and no `osm2pgsql` involved.

```sh
python3 demo/ground_truth/osm/fetch_osm.py \
    --bbox 52.5135 13.3750 52.5225 13.3950 --out-dir demo/ground_truth/osm

./build/out/mapnik-ground-truth-render demo/ground_truth/osm/osm.xml \
    demo/ground_truth/osm/berlin \
    --size 1200 900 --extent-wgs84 13.3750 52.5135 13.3950 52.5225 \
    --plugins build/out/plugins/input --fonts fonts/dejavu-fonts-ttf-2.37/ttf \
    --identity osm_id --identity osm_type --no-attributes --simplify 1.0

python3 demo/ground_truth/annotate.py \
    demo/ground_truth/osm/berlin.png demo/ground_truth/osm/berlin.json \
    demo/ground_truth/osm/berlin.annotated.png --layer roads --no-text
```

Rendering the real openstreetmap-carto stylesheet instead would additionally
need PostGIS, `osm2pgsql` and the `carto` compiler; nothing in the extraction
itself changes, since it only observes the AGG pass.

## The real OpenStreetMap Standard stylesheet

`osm-carto/setup.sh` builds the genuine `openstreetmap-carto` stack in
containers - PostGIS, an osm2pgsql import, the Noto fonts, and `carto` to
compile the CartoCSS into Mapnik XML - then hands you a stylesheet that
`mapnik-ground-truth-render` reads like any other. Roughly an hour and ~6 GB,
almost all of it the coastline shapefile.

```sh
sh demo/ground_truth/osm-carto/setup.sh
./build/out/mapnik-ground-truth-render ~/.cache/mapnik-ground-truth/osm-carto/openstreetmap-carto/osm-carto.xml \
    demo/ground_truth/osm-carto/pariser-platz \
    --size 699 536 --extent-wgs84 13.3745 52.5145 13.3820 52.5180 \
    --plugins build/out/plugins/input \
    --fonts ~/.cache/mapnik-ground-truth/osm-carto/fonts \
    --identity osm_id --simplify 1.0 --candidates
```

Compare against the real tiles with `osm/fetch_osm_tiles.py`, which downloads
and stitches `tile.openstreetmap.org` for the same bbox. That image is a
*reference only*: a rendered tile has no element identity, so no ground truth
can be derived from it - it has to be re-rendered to be described.

Two things specific to this stylesheet:

* **Identity has to be added.** Upstream's queries select no identifier at
  all, so out of the box every visible object is anonymous.
  `osm-carto/patch_project_queries.py` adds `osm_id` to every datasource query
  that can take one - all 79 except `junctions`, which merges several
  motorway-junction nodes into one label with `ST_Centroid(ST_Collect(way))`
  and `GROUP BY`, so no single id exists for it.

  Always verify rather than assume: pass `--require-identity osm_id` and the
  renderer reports coverage per layer and exits non-zero if anything is
  missing. From Python, `result.identity_coverage("osm_id")` returns
  `(found, total, {layer: missing})`; in SPARQL, `q12-identity-coverage.rq`.

  ```
  identity    1079 of 1079 objects carry 'osm_id'
  identity    11566 of 11567 objects carry 'osm_id'  -- MISSING on 1:
                1  layer 'junctions'
  ```
* **Casing and fill are separate stylesheet layers** (`roads-casing`,
  `roads-fill`), not two symbolizers on one rule. Objects aggregate by
  (layer, feature id), so one road appears as two visible objects; the shared
  `osm_id` is what joins them back together.

## From Python

`mapnik_ground_truth.py` calls the renderer in-process through ctypes, so there
is no subprocess and no temp file. It needs nothing installed; Pillow and numpy
are used only if you ask for `result.image` or `result.array`.

```python
import sys; sys.path.insert(0, "demo/ground_truth")
import mapnik_ground_truth as gt

gt.setup(plugin_dir="build/out/plugins/input",
         font_dir="fonts/dejavu-fonts-ttf-2.37/ttf")

result = gt.render("demo/ground_truth/osm/osm.xml",
                   width=1200, height=900,
                   extent_wgs84=(13.3750, 52.5135, 13.3950, 52.5225),
                   identity=["osm_id", "osm_type"],
                   simplify_tolerance=1.0)

result.save("map.png")
print(result.displayed_element_count, "displayed,",
      result.invisible_element_count, "drawn but invisible")

for obj in result.labelled():
    print(obj.layer, obj.feature_id, obj.identity["osm_id"],
          repr(obj.label), obj.visible_pixel_count)
    for ring in obj.rings():          # exterior first, then holes
        ...
```

`render()` also takes the stylesheet as a string (`is_string=True` plus
`base_path=` for relative datasource paths), `extent=` in the map's own srs,
`scale_factor=`, and `collect_attributes=True` to keep source tags. Failures
raise `GroundTruthError` — no exception crosses the C boundary.

If your program already builds maps with python-mapnik, `mapnik.save_map(m, path)`
turns one into XML that `render()` accepts. Note that a python-mapnik installed
from packages links its own libmapnik; this module talks to the one built from
this tree, so the two are independent.

The library is found via `MAPNIK_GROUND_TRUTH_LIB`, then next to the module,
then `build/out/`. Override explicitly with `gt.render(..., library="/path/to.so")`.

## RDF / SPARQL

`rdf/ground_truth_to_rdf.py` maps the JSON onto the CartoGraph vocabulary in
`map-display-ontology/`. Needs `rdflib` (and `pyshacl` for the validator).

```sh
python3 demo/ground_truth/rdf/ground_truth_to_rdf.py \
    demo/ground_truth/osm/pariser-platz.json \
    -o demo/ground_truth/rdf/pariser-platz.ttl \
    --base https://example.org/berlin/ \
    --source-geojson demo/ground_truth/osm

python3 demo/ground_truth/rdf/validate_and_query.py demo/ground_truth/rdf/pariser-platz.ttl
```

| JSON | RDF |
|---|---|
| `map` | `cg:MapImage` + `cgp:MapConfiguration` |
| `elements[]` | `cg:PointElement` / `cg:LineElement` / `cg:PolygonElement`, by `anchor_dimension` |
| `elements[].portrayal` | `cg:StrokePortrayal`, `cg:FillPortrayal`, `cg:TextPortrayal`, `cg:MarkerPortrayal`, `cg:DotPortrayal`, `cg:PatternPortrayal`, `cg:ShieldPortrayal` |
| `objects[]` | `cg:VisibleObject` with `cg:hasDisplayedElement` |
| `objects[].visible_properties.label` | `cg:visibleLabel` — image-grounded |
| `objects[].source_properties` | predicates on the `geo:Feature`, never on the visible object |

The RDF CLI also links the map to its rendered image with
`cg:imageUrl <area01.png>`. By default it uses the PNG alongside the input JSON
and writes its path relative to the output Turtle document. Moving the PNG and
Turtle together preserves the link. Use `--image-url ../images/area01.png` to
override it, or pass `image_url=` when using `Converter` directly. Filenames
are URI-escaped; non-Turtle output uses an absolute image IRI.

Run the RDF converter regression tests with
`python -m unittest discover -s demo/ground_truth/rdf -p 'test_*.py'`.

`--source-geojson` joins the original GeoJSON back in so every `geo:Feature`
gets a `geo:hasGeometry`; the ground truth itself deliberately carries no source
geometry, and the shapes treat it as optional.

Render with `--candidates` to also record the occurrences that left *no* visible
pixel. They become `cg:CandidateElement` with a `cg:visibilityStatus` of
`cg:Occluded` (drawn, then covered) or `cg:OutsideViewport` (never rasterized in
view), and are deliberately *not* `cg:DisplayElement`s, so nothing can mistake
them for something visible. The converter keeps them by default; pass
`--no-candidates` to drop them.
Collision-rejected labels cannot be represented: mapnik discards them before
the renderer ever sees them, so `cg:Suppressed` is never emitted.

`--infer-cased-lines` groups a road's casing and fill strokes into a
`cg:CasedLinePortrayal`. Mapnik has no notion of casing - it is two line
symbolizers on one rule - so this is a documented heuristic (consecutive stroke
elements of one object whose width strictly decreases), not something the
renderer reported.

Pixel coordinates are canvas coordinates: origin top-left, y growing downwards,
boxes half-open, rings on pixel corners.

## ATKIS RDF inputs

For a shared Berlin/Brandenburg batch across DTK50, OSM Standard, OpenTopoMap,
and Humanitarian OSM, see [`berlin-batch/README.md`](berlin-batch/README.md).
The first five-area batch includes maps, knowledge graphs, aligned official
OSM service references, and an interactive comparison gallery. DTK50 outputs
are explicitly marked incomplete previews with per-map coverage reports.
The batch now has an [ADV-wrapper acquisition path](berlin-batch/README.md)
using `https://wunderfacts.com/adv/`. Provider failures are reported explicitly;
there is no automatic fallback to InspireWrapper or direct WFS.

The companion `otto-usecase-3/data-pipeline/gpkg-to-dtk50` project now supports
direct RDF snapshots through `map-fetch-inspire` and `map-render-rdf`; see its
`docs/rdf-input.md` for setup and completeness checks. The converter accepts
JSON-encoded `source_properties.source_uris` arrays and emits
`prov:wasDerivedFrom` links to those original resources. Optional top-level
`source_geometries` records attach CRS-qualified WKT, while `coverage` records
whether the output is complete or an explicitly requested preview.

## Annotator options

`--layer NAME` (repeatable), `--labelled-only`, `--min-pixels N`, `--no-text`,
`--bbox`, `--bbox-only`, `--fill-alpha`, `--line-width`, `--list N`.
