# 25 DTK50 maps and knowledge graphs from each of four DLM50 states

This batch uses the separate OTTO `adv-dlm50-adapter`, the shared
`gpkg-to-dtk50` preparation pipeline and the corrected Mapnik DTK50 stylesheet.
It generates 100 distinct 640×640 maps at a 1:50,000 scale denominator, with
exact EPSG:3857 extents, Mapnik ground-truth JSON and CartoGraph RDF.

The four live ADV dump roots were checked on 2026-09-09:

| State | Pinned route | Required geometric object prefix |
|---|---|---|
| NRW | `dlm50:nrw` | `DENW` |
| Saxony | `dlm50:nrw` | `DESN` |
| Hesse | `dlm50:hessen` | `DEHE` |
| Rhineland-Palatinate | `dlm50:rheinland-pfalz` | `DERP` |

Saxony sharing the `nrw` registry key is a server registration detail, not a
fallback to NRW data. Each returned geometry's object prefix is checked.
All raw data comes from https://wunderfacts.com/adv/; the source-adapter does
not fall back to another provider.

## Prepare and run

From the Mapnik root, with the neighboring OTTO components and their virtual
environment installed, save the official Eurostat state boundaries at
`build/dlm50-100/state-boundaries.geojson` from:

https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/NUTS_RG_01M_2024_4326_LEVL_1.geojson

```bash
/home/rene/Documents/otto-usecase-3/data-pipeline/gpkg-to-dtk50/.venv/bin/python \
  demo/ground_truth/dlm50-batch/prepare.py build/dlm50-100

/home/rene/Documents/otto-usecase-3/data-pipeline/gpkg-to-dtk50/.venv/bin/python \
  demo/ground_truth/dlm50-batch/run.py build/dlm50-100 --workers 4
```

The selected named-area centers mix cities, smaller settlements and surrounding
landscapes. `prepare.py` rejects any acquisition bbox (including its 250 m buffer)
that crosses the GISCO state polygon. The chosen terrain classes are explicit
approximate regional assumptions, not per-feature surveyed classifications.

A single acquisition client spaces uncached HTTP requests 12 seconds apart.
Geometry pages contain up to 2,000 objects. Road-parent batches contain up to
512 objects and use the wrapper's form-encoded SPARQL POST to avoid GET URI
limits. This transport was live-tested with 512 distinct parents. Every requested
parent must have the expected ID, AX_Strasse type, classification and DLM50 model.
GET and POST responses are checksummed and cached; request forms are archived.

Matching existing acquisition snapshots are reused by symlink. New snapshots
are stored under `snapshots/<area>/<state>`. Incomplete road acquisition is
resumed before a map may render. Already validated map/graph pairs are reused
when restarting the same batch. A file lock prevents concurrent coordinators
from writing into one batch. Existing outputs should be preserved in another
batch directory when changing the selected areas or rendering implementation.

## Validation and publication

For church detection annotations, use `church-bboxes.rq` against each map graph.
It groups church-symbol elements by map and original ADV feature URI and takes
the enclosing pixel box. Element IDs and layer-scoped VisibleObjects are drawing
identities, not unique churches: the existing exports can contain both RUL00040
and RUL00095 symbols for the same source and anchor. The query preserves the
number of contributing drawing elements for auditing. This corrects annotation
counting for existing graphs; it does not remove duplicate rendering upstream.
It defines one target per source church per map, excluding footprints and labels;
separate symbol placements for a single source would share one enclosing box.

Four bounded workers render and validate independently while acquisition
continues. A map is published only after its graph passes the existing CartoGraph
SHACL shapes, source provenance, image dimensions, viewport, image-link and
visible-object-count checks. Extra checks reject blank/sparse maps and unresolved
road acquisition. A successful validation does not establish cartographic fidelity
or overall DLM50 completeness; coverage reports remain attached.

`public/` is the web root, with a live gallery, per-area PNG/JSON/Turtle/coverage/
validation files, a dataset index, checksums and per-state ZIP archives when all
25 pairs in that state are ready. Raw request caches are outside that web root.
Graphs use relative `cg:imageUrl <map.png>` links, so each graph resolves to its
paired image in the same directory. No new official reference rasters are downloaded.

## Export flat map/graph pairs

```bash
python3 demo/ground_truth/dlm50-batch/export_pairs.py \
  build/dlm50-100 build/dlm50-100/public/pairs --watch
```

This exports validated areas as `cologne-map.png`, `cologne-map.ttl`, etc., all
in one folder. The Turtle image IRI becomes `<cologne-map.png>`, relative to
the graph, so the pair remains linked when the folder is moved or hosted.
Other RDF content and the original files are preserved. Names use the unique
area slugs from `progress.json`, including location qualifiers where present.
Without `--watch`, the command exports only the pairs currently ready. With
`--watch`, it adds newly validated pairs every 30 seconds until all are ready.
Rerunning the command refreshes changed exports and leaves identical files alone.

The exporter also detects MapTrace batches with `dataset.json` tile slugs and
uses their validated final `g_f.ttl` graphs. For multiple tiles in one city:

```bash
python3 demo/ground_truth/dlm50-batch/export_pairs.py \
  /home/rene/Documents/MapTrace/outputs/cartograph-100-maps \
  /home/rene/Documents/MapTrace/outputs/cartograph-100-maps/pairs \
  --name-prefix nuremberg-
```

For this workspace, `dtk50-gallery.service` exposes the gallery through Tailscale:

http://100.84.50.110:8770/batch-100/

The running generation unit is `dlm50-100-batch.service`. Monitor with:

```bash
systemctl --user status dlm50-100-batch.service
journalctl --user -u dlm50-100-batch.service -f
```

Progress and failures are recorded in `progress.json`; detailed render/validation
logs are in `logs/<area>.log`. The coordinator exits nonzero if fewer than 100
pairs finish. Inspect any failed area before retrying; cached success responses
and already validated outputs are retained.
