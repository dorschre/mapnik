# Berlin and Brandenburg map batch

## Four-state 100-map batch

See [the four-state batch](../dlm50-batch/README.md) for 25 DTK50 maps and knowledge
graphs each from NRW, Saxony, Hesse and Rhineland-Palatinate, with resumable
acquisition, bounded rendering workers and a live validated-output gallery.

## ADV adapter component

ADV acquisition and RDF decoding now live in the separate OTTO component:
[adv-dlm50-adapter](../../../../otto-usecase-3/data-pipeline/adv-dlm50-adapter/README.md),
at `/home/rene/Documents/otto-usecase-3/data-pipeline/adv-dlm50-adapter`.
It owns DLM50 pagination, cached HTTP requests, road-parent retrieval, snapshot
repair, and `AdvFeatureSource`. Its acquisition commands work without Mapnik;
the source-reader integration optionally uses `gpkg-to-dtk50`.

`fetch_adv.py`, `adv_source.py`, `enrich_road_parents.py`, and
`repair_acquisition.py` here are compatibility wrappers. `compare_states.py`
retains the demo's area/terrain choices and delegates acquisition to the package.
The loader uses an installed package first, or the neighboring OTTO checkout.
For other layouts, install `adv-dlm50-adapter` into the rendering environment.
Mapnik styles, render/KG scripts and reference-map comparisons stay here; existing
generated maps and snapshots are unchanged.

Run the moved adapter tests separately:

```bash
ADAPTER=/home/rene/Documents/otto-usecase-3/data-pipeline/adv-dlm50-adapter
PIPELINE=/home/rene/Documents/otto-usecase-3/data-pipeline/gpkg-to-dtk50
PYTHONPATH="$ADAPTER/src${PYTHONPATH:+:$PYTHONPATH}" "$PIPELINE/.venv/bin/python" \
  -m unittest discover -s "$ADAPTER/tests" -p 'test_*.py'
```

## NRW / Saxony DLM50 comparison

### Stadiums, road shields and railway symbols

`build/dtk50-symbol-fixes/index.html` compares revised Dresden and Cologne maps
with the acquisition-repaired previews and official references, including the
reported stadium, sports-field and railway crops. Both revised maps have newly
extracted knowledge graphs and validation reports. The other eight maps in the
earlier batch have not been rerendered for this presentation-mode change.

The previous `stdpraes` setting was too restrictive: it excluded point stadiums,
small sports fields, route shields, railway detail symbols and labels that the
catalog marks `praesobj`. `render_dtk50.py` now defaults to the existing
`stdpraes_praesobj` mode, with `include_ap=False`. This supersedes the earlier
interpretation below that all missing labels required upstream changes.
`--presentation-mode stdpraes` reproduces the former mode.

The Mapnik adapter also fits source-sized SVGs using their painted catalog
extent. Applying a painted width directly to Mapnik's unstroked path bounds
enlarged stadium strokes twice. Supplied feature sizes, orientations, geometry,
and placement are preserved. The regression tests check actual rendered pixels.

```bash
PIPELINE=/home/rene/Documents/otto-usecase-3/data-pipeline/gpkg-to-dtk50
export MAPNIK_FONT_DIRS="$PIPELINE/src/map_symbology/resources/atkis/text/fonts/univers:$PWD/fonts/dejavu-fonts-ttf-2.37/ttf"
"$PIPELINE/.venv/bin/python" demo/ground_truth/berlin-batch/render_dtk50.py \
  build/dtk50-acquisition-fixed --only dresden cologne --allow-incomplete-preview \
  --output-root build/dtk50-symbol-fixes
```

For validation/reporting of a staged subset, copy only its selected records from
the original `areas.json` into the output root, plus each unchanged
`reference.png` and `reference.json`; then run `validate_batch.py` and
`presentation_report.py build/dtk50-acquisition-fixed build/dtk50-symbol-fixes`.
The frozen-input `restyle_dtk50.py` also respects each input's presentation mode.

These two examples are not faithful official reproductions. Combined mode exposes
crowded shield/label placements and context-fitted sports symbols. Cologne's
railway fan includes up to nine separate overlapping source lines at one pixel;
changing a stylesheet cannot perform the missing cartographic generalization.
See the output `FINDINGS.md` for exact source IDs, catalog rules and remaining
limitations. No `gpkg-to-dtk50` logic was changed.

### Repairing road acquisition

ADV bounding-box responses contain road axes but omit their non-geometric
`AX_Strasse` parents. The catalog needs those parents' `widmung` classifications.
`compare_states.py` now retrieves missing parents automatically after geometry
pagination. `enrich_road_parents.py` uses the wrapper's documented SPARQL
`CONSTRUCT` with up to 64 `FROM <oid/...>` documents per request. Every requested
parent must have the exact ID, `AX_Strasse` type, a classification, and DLM50 model
membership; incomplete or wrong-model responses fail before installing a snapshot.
Successful HTTP responses are cached and checksummed. No pipeline logic changes
or guessed road classifications are involved.

To repair the existing ten-area batch while preserving its inputs and outputs:

```bash
PIPELINE=/home/rene/Documents/otto-usecase-3/data-pipeline/gpkg-to-dtk50
export MAPNIK_FONT_DIRS="$PIPELINE/src/map_symbology/resources/atkis/text/fonts/univers:$PWD/fonts/dejavu-fonts-ttf-2.37/ttf"
"$PIPELINE/.venv/bin/python" demo/ground_truth/berlin-batch/repair_acquisition.py \
  build/dtk50-state-comparison build/dtk50-acquisition-fixed
"$PIPELINE/.venv/bin/python" demo/ground_truth/berlin-batch/render_dtk50.py \
  build/dtk50-acquisition-fixed --allow-incomplete-preview
.venv/bin/python demo/ground_truth/berlin-batch/validate_batch.py \
  build/dtk50-acquisition-fixed --products dtk50
.venv/bin/python demo/ground_truth/berlin-batch/acquisition_report.py \
  build/dtk50-mapnik-fixes build/dtk50-acquisition-fixed
```

The report reuses the existing exact-grid official references and compares
before/official/after images. Inspect the new images before accepting them.
`acquisition-repair.json` records road closure per area; `snapshots/` contains
the repaired RDF and manifests, and `request-cache/` retains raw parent responses.
Existing Wermsdorf parents are reused. An interrupted repair can be rerun with
the same arguments and cached responses. Overall completeness stays false:
resolving road parents does not supply missing NRW buildings/contours or correct
the pipeline's label and symbol-placement behavior.

### Mapnik stylesheet fixes with frozen inputs

`build/dtk50-mapnik-fixes/index.html` shows before/official/after triples for all
ten areas. The fixes live in this Mapnik repository's `dtk50_stylesheet.py`;
no `gpkg-to-dtk50` files, matching rules, source selection, placement or
generalization logic are changed. `render_dtk50.py` uses this stylesheet adapter
for subsequent renders. Existing outputs are preserved in the original batch.

The adapter keeps fractional catalog stroke widths, dash lengths and font sizes
instead of the raster backend's integer rounding and minimum sizes. Ordinary
SVG markers use their native catalog coordinates and physical scale: fitting a
stroke-inclusive raster dimension to Mapnik's path-only bounding box enlarged
the stroke a second time. The Mapnik transform also restores the catalog's
uniform graphic scale factors omitted by the SVG exports (e.g. 0.75 for tree
rows and 0.85 for monuments); these are specification values, not empirical
reference-fitting factors. Mixed per-member scales are rejected explicitly
because they require a corrected SVG resource, rather than a uniform transform.
Positive screen-space rotations now map to positive
AGG marker rotations; text orientation retains its different sign convention.
Source-sized symbols, route shields, feature filters and collision policy are
preserved. Physical sizes use the ground resolution of the actual Mapnik
viewport, not the larger UTM bounding envelope of the reprojected viewport.
The difference in this batch is 1.6–6.7%. Existing surface-pattern resources
are preserved and their Mapnik transform adjusts their spacing to that scale.

```bash
PIPELINE=/home/rene/Documents/otto-usecase-3/data-pipeline/gpkg-to-dtk50
export MAPNIK_FONT_DIRS="$PIPELINE/src/map_symbology/resources/atkis/text/fonts/univers:$PWD/fonts/dejavu-fonts-ttf-2.37/ttf"
"$PIPELINE/.venv/bin/python" demo/ground_truth/berlin-batch/restyle_dtk50.py \
  build/dtk50-state-comparison build/dtk50-mapnik-fixes
.venv/bin/python demo/ground_truth/berlin-batch/validate_batch.py \
  build/dtk50-mapnik-fixes --products dtk50
.venv/bin/python demo/ground_truth/berlin-batch/stylesheet_report.py \
  build/dtk50-state-comparison build/dtk50-mapnik-fixes
"$PIPELINE/.venv/bin/python" -m unittest discover \
  -s demo/ground_truth/berlin-batch -p 'test_*.py'
```

The restyling command only reads the frozen datasources. It verifies their
hashes and preserves all layer definitions and rule filters. Each new map has
a newly extracted JSON and RDF graph. The XML still references the original
datasources and symbol resources by absolute path. `stylesheet-changes.json`
records before/after hashes, and `input-audit.json` records exactly what Mapnik
received. The pixel regression tests invoke the actual Mapnik renderer and
demonstrate the old oversizing and reversed rotation before checking the fix.

These outputs are **not faithful DTK50 reproductions**. There are no emitted
label features in these ten frozen inputs. Wermsdorf contains 19 hospital-marker
features before Mapnik draws anything. Missing NRW buildings/contours and many
unclassified roads also precede stylesheet application. Those issues require
upstream input or pipeline work and are outside the requested Mapnik-only
scope. `VISUAL_REVIEW.md` in the output directory records the manual review;
new renders must be inspected again rather than inheriting that review.

### Original acquisition and rendering

`build/dtk50-state-comparison/index.html` compares five areas in each state.
This separate batch uses ADV DLM50 dumps, 640 × 640 pixels at zoom 14, and the
same DTK50 scale denominator (50,000) and 250 m acquisition buffer in both states.
The areas cover roughly 4 km per side, depending on latitude. Pairs represent
broad landscape categories, not identical geography or statistical controls.

Both dumps are currently registered under `dlm50:nrw` in ADV, including the
Saxony dump path. The downloader verifies every geometric object's DENW or DESN
prefix. Dump pagination is global (500 geometric objects per page), unlike
the live WFS per-type pagination; repeated pages and wrong-state objects fail.
Cache files preserve original HTTP bytes and metadata. The decoding adapter
supplies a missing `gml` namespace declaration, recognizes AdV UTM CRS aliases
and metre units, decodes structured `AX_Lagebezeichnung/unverschluesselt`
place names, and checks native GML against the supplied WGS84 geometry.
The original RDF graph remains unchanged. No missing coordinates are invented.

```bash
export PIPELINE=/home/rene/Documents/otto-usecase-3/data-pipeline/gpkg-to-dtk50
export BATCH="$PWD/build/dtk50-state-comparison"
export MAPNIK_FONT_DIRS="$PIPELINE/src/map_symbology/resources/atkis/text/fonts/univers:$PWD/fonts/dejavu-fonts-ttf-2.37/ttf"
"$PIPELINE/.venv/bin/python" demo/ground_truth/berlin-batch/compare_states.py "$BATCH"
"$PIPELINE/.venv/bin/python" demo/ground_truth/berlin-batch/render_dtk50.py \
  "$BATCH" --snapshots "$BATCH/adv-snapshots" --allow-incomplete-preview
.venv/bin/python demo/ground_truth/berlin-batch/validate_batch.py "$BATCH" --products dtk50
.venv/bin/python demo/ground_truth/berlin-batch/fetch_dtk50_references.py "$BATCH"
.venv/bin/python demo/ground_truth/berlin-batch/state_report.py "$BATCH"
```

The paired gallery links PNG/JSON/Turtle, coverage, and validation for every
area. `comparison.json` compares feature counts, decoding errors, rule failures,
unresolved references, and graph checks. `dataset.jsonl` indexes all ten pairs;
`SHA256SUMS` hashes their principal outputs. Source totals and schemas remain
unverified, so these are previews even when graph validation passes. This
comparison includes ten official DTK50 raster references via ADV's `/wms`
proxy. NRW uses `nw_dtk50_col`; Saxony uses `sn_dtk50_p_color`. Each request
pins the official service and layer, with the local EPSG:3857 bbox and 640 × 640
dimensions. ADV's `crs` parameter governs bbox coordinates too: passing longitude
and latitude with `crs=EPSG:3857` produces a blank response. Blank or wrongly sized
images are rejected. Responses and HTTP metadata are cached with checksums;
no local image cropping, resampling, or mosaicking is needed. No automatic
basemap selection is used. `reference.png` / `reference.json` live beside each
local map, and the gallery has a comparison slider and source attribution.
The existing knowledge graphs annotate only the local renders, not the official
rasters. Download timestamps do not establish the upstream map edition date.
New OSM maps are not part of this comparison.

### Catalog configuration correction

`render_dtk50.py` uses `presentation_mode='stdpraes'` and `include_ap=False`
for these DLM50 snapshots. The previous `all` mode emitted `praesobj`-only
symbols without their cartographic placement records (for example electrical
railway markers, stops, and duplicate hospital symbols). Standard presentation
preserves ordinary linework while respecting the catalog's emission permissions.
It does not reconstruct missing presentation objects or official generalization.

Contour rules require an explicit `terrain_zone` on each area. The state-area
generator supplies regional hill/mountain categories for the Saxony examples;
these are area-level cartographic assumptions recorded with source links in
`areas.json` and each coverage report. Wermsdorf's hill category follows the
[Sachsenforst regional description](https://www.wald.sachsen.de/54_Horstsee_Wermsdorf.pdf).
Missing terrain context is now reported explicitly rather than silently
producing no contours.

Wermsdorf's road axes lacked their referenced `AX_Strasse` classifications.
The correction retrieves those original parent records from ADV, preserving the
initial snapshot separately. Reproduce the enrichment and select it explicitly:

```bash
"$PIPELINE/.venv/bin/python" demo/ground_truth/berlin-batch/enrich_road_parents.py \
  "$BATCH/adv-snapshots/wermsdorf/Saxony" \
  "$BATCH/enriched-snapshots/wermsdorf/Saxony" --cache "$BATCH/road-parent-cache"
"$PIPELINE/.venv/bin/python" demo/ground_truth/berlin-batch/render_dtk50.py \
  "$BATCH" --snapshots "$BATCH/enriched-snapshots" --only wermsdorf --allow-incomplete-preview
```

The existing review batch also records Wermsdorf's `snapshot_directory` in
`areas.json`, so subsequent rerenders retain that enriched input. Other areas
still report their unresolved parent records; they are not silently classified
using guessed defaults. Outputs preceding this correction are archived under
`build/dtk50-state-comparison/before-catalog-fix/`.

## Berlin batch

The first batch is in `build/berlin-five/index.html`: five shared areas, four
products, 20 PNG/ground-truth JSON/Turtle pairs, and 15 official OSM product
reference images. Open the HTML directly in a browser. It includes a comparison
slider and links to every graph, image, manifest, and coverage report.

**ADV update (2026-09-09):** acquisition now uses
`https://wunderfacts.com/adv/`, with no InspireWrapper/direct-WFS fallback.
Potsdam and Bernau have replacement ADV-based maps and graphs. The wrapper's
Berlin route reports `SSLHandshakeException: Remote host terminated the handshake`
and the pinned query rejects that service as unregistered. Mitte, Charlottenburg,
and Köpenick therefore still show their explicitly labelled previous WFS previews.
See `build/berlin-five/adv-snapshots/acquisition-report.json`. Previous Potsdam and
Bernau WFS outputs are archived under `build/berlin-five/previous-wfs/`.

The queried ADV DLM50 endpoint has no coverage for Berlin; the working
Brandenburg route supplies Basis-DLM. ADV responses provide native GML and WGS84
WKT for the same objects: `adv_source.py` checks agreement within 2 cm and retains
the native geometry and original ADV object IRIs. Both available areas required
pagination beyond 500 road-axis features. Referenced parent records are not
included in these responses (241 distinct references in Potsdam, 230 in Bernau),
so they remain unresolved and explicitly reported; these previews do not replace
complete DTK50 inputs. No completeness is inferred from HTTP success.

Areas: Berlin Mitte, Charlottenburg, Köpenick, Potsdam, and Bernau bei Berlin.
The views are 640 × 640 at OSM zoom 15, roughly 1.9 km per side on the ground.
Centers are snapped to whole global slippy-map pixels. Every product is rendered
in EPSG:3857 at the same exact extent. Reference tiles are merged and cropped on
integer pixel boundaries, without resampling. Ground-truth geometries describe
the local render; they must not be treated as annotations of the reference image.

## DTK50 status

The five DTK50 outputs are **incomplete previews**, using the implemented DTK50
catalog at scale denominator 50,000 with official Berlin/Brandenburg Basis-DLM
snapshots. They are not complete reproductions of the official DTK50 product.
Berlin lacks parts of the catalog input model, including building and
presentation classes; Brandenburg also has missing context/attributes.
The strict Mitte run rejected the input. Each preview records its actual
limitations in `coverage-report.json`, ground-truth JSON, and RDF.

The default renderer below is strict. Pass `--allow-incomplete-preview` explicitly
to reproduce this first review batch. The large symbols in these small DTK50
windows follow the existing catalog sizing; no extra visual calibration is
applied. The local `atkis.gpkg` covers Saxony and is not used for Berlin.

## Existing prerequisites on this workstation

- Mapnik built in `build/out`, including its ground-truth renderer and input plugins.
- Mapnik `.venv/bin/python` with Pillow, rdflib, and pyshacl.
- `/usr/bin/python3` with GDAL, lxml, PyYAML, and psycopg2.
- The companion pipeline's `.venv`, with its installed `map_symbology` and GDAL.
- Running `openstreetmap-carto-db-1` at localhost:54321, database `gis`, user
  `postgres`, with OSM data covering all areas and a buffer. This batch reused
  the existing database; it did not replace or update its import.
- OSM Standard checkout at
  `/home/rene/Documents/maptrace-carto-reference/openstreetmap-carto`, including fonts.
- Pinned OpenTopoMap/Humanitarian checkouts in `build/map-products/upstream`,
  and the installed `carto-cli:latest` Docker image. See
  [map-products setup](../map-products/README.md) if they need installing.

The OSM Standard source revision used was
`7d2926a85acd07c0a4051f3d444ebe2b59c0676e`. Other OSM product revisions,
elevation checksums and materialized datasource counts are in their manifests.
The existing OSM database's exact source replication timestamp was not verified.
Differences from the live reference service can reflect data age, style revision,
terrain processing, and tile/metatile label placement.

## Reproduce

Run from the Mapnik repository root. These commands write the batch directory
and reuse the existing database and caches. They do not initialize a database.

```bash
export MAPNIK_ROOT="$PWD"
export PIPELINE=/home/rene/Documents/otto-usecase-3/data-pipeline/gpkg-to-dtk50
export CARTO_SOURCE=/home/rene/Documents/maptrace-carto-reference/openstreetmap-carto
export BATCH="$MAPNIK_ROOT/build/berlin-five"
export MAPNIK_FONT_DIRS="$PIPELINE/src/map_symbology/resources/atkis/text/fonts/univers:$MAPNIK_ROOT/fonts/dejavu-fonts-ttf-2.37/ttf"

.venv/bin/python demo/ground_truth/berlin-batch/prepare_areas.py "$BATCH"

"$PIPELINE/.venv/bin/python" demo/ground_truth/berlin-batch/fetch_adv.py "$BATCH"

"$PIPELINE/.venv/bin/python" demo/ground_truth/berlin-batch/render_dtk50.py \
  "$BATCH" --snapshots "$BATCH/adv-snapshots" --allow-incomplete-preview

/usr/bin/python3 demo/ground_truth/berlin-batch/prepare_standard.py \
  --upstream "$CARTO_SOURCE" --output "$BATCH/osm-standard-style"
/usr/bin/python3 demo/ground_truth/berlin-batch/render_standard.py "$BATCH" \
  --style "$BATCH/osm-standard-style/style.xml" --fonts "$CARTO_SOURCE/fonts"

/usr/bin/python3 - <<'PY'
import json, os, subprocess
from pathlib import Path
batch = Path(os.environ['BATCH'])
for area in json.loads((batch / 'areas.json').read_text()):
    subprocess.run([
        '/usr/bin/python3', 'demo/ground_truth/map-products/render.py',
        '--product', 'both', '--center', str(area['lon']), str(area['lat']),
        '--zoom', str(area['zoom']), '--size', str(area['size']),
        '--output', str(batch / area['name']),
    ], check=True)
PY

.venv/bin/python demo/ground_truth/berlin-batch/fetch_references.py "$BATCH"
.venv/bin/python demo/ground_truth/berlin-batch/validate_batch.py "$BATCH"
.venv/bin/python demo/ground_truth/berlin-batch/gallery.py "$BATCH"
.venv/bin/python demo/ground_truth/berlin-batch/finalize.py "$BATCH"
```

While the Berlin ADV route is unavailable, acquisition and rendering exit
nonzero for those areas and do not substitute another source. To render only
the available replacements, add `--only potsdam bernau`; `--output-root` stages
them in a separate directory for review. `--status-file` can reuse a captured
ADV status response, but omit it when checking whether a failed service recovered.

Preserve `reference-cache` and the snapshot directories. The ADV downloader
verifies checksums when reusing successful cached responses, spaces uncached
requests 12 seconds apart, and keeps raw HTTP metadata. Snapshots under
`snapshots/` belong to the earlier WFS run; `adv-snapshots/` are the new source.
`render_standard.py` collects
datasource attributes because PostGIS otherwise omits `osm_id` from the feature
query. Do not add `--no-attributes` to this path.

DTK50 matching happens in the pipeline's native EPSG:25832. Its generated layers
keep that CRS; Mapnik reprojects them into the common EPSG:3857 viewport. Original
RDF source geometries retain their own CRS and `prov:wasDerivedFrom` links.

## Files and checks

Each `<area>/<product>/` contains `map.png`, `map.json`, `map.ttl`, and a manifest.
OSM products also have `reference.png` and `reference.json`. These references
come from `tile.openstreetmap.org`, `a.tile.opentopomap.org`, and
`a.tile.openstreetmap.fr/hot` respectively. No fallback product is substituted.
The reference JSON records tile URLs, checksums, crop, extent, and attribution.
The root `dataset.jsonl` indexes every map/graph/reference pair for programmatic
use; `manifest.json` summarizes counts and `SHA256SUMS` records content hashes.

`validation.json` and per-product `validation.json` / `shacl-report.txt` record
graph parsing, SHACL validation without inference, matching object counts,
geographic viewport, image links, image dimensions, and source identity checks.
SHACL conformance does not establish DTK50 input completeness or visual fidelity.

Regression checks:

```bash
.venv/bin/python -m unittest discover -s demo/ground_truth/berlin-batch -p 'test_batch.py' -v
"$PIPELINE/.venv/bin/python" -m unittest discover -s demo/ground_truth/berlin-batch -p 'test_adv_adapter_integration.py' -v
/usr/bin/python3 -m unittest discover -s demo/ground_truth/map-products -p 'test_*.py' -v
```

Potsdam exposed empty LineStrings returned by the Humanitarian administrative
boundary query. The exporter now skips empty geometries before writing GeoJSON;
Mapnik's GeoJSON input rejects those empty coordinate arrays. The regression
test covers the failure and preserves real geometry, including the origin.

## Expanding to 100 maps

Pass a JSON list of `{name, label, lon, lat, states}` to
`prepare_areas.py --centers centers.json`. Rendering loops over that list. Keep
each full view and acquisition buffer within one state; this wrapper does not
mosaic cross-border ATKIS snapshots. Verify database coverage for new OSM areas.
The same approach yields 100 maps per product from 100 shared area records;
this first batch intentionally contains only five per product.

Resolve the DTK50 completeness gaps before treating a larger batch as complete
DTK50 training/evaluation data. The public reference downloader is deliberately
limited to five requested review views; arrange suitable service access for a
larger reference dataset. The [OSM tile policy](https://operations.osmfoundation.org/policies/tiles/)
prohibits bulk/prefetch downloads. See also
[OpenTopoMap usage](https://opentopomap.org/about) and
[OSM France map use](https://www.openstreetmap.fr/fonds-de-carte/).
Keep each product's attribution visible when sharing maps and references.
