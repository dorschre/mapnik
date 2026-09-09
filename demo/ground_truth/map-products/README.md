# OpenTopoMap and Humanitarian OSM

Render both products through Mapnik with per-object ground truth and Turtle RDF.
The styles come from pinned upstream projects, rather than recoloring OSM Carto:

| Product | Upstream | Revision |
| --- | --- | --- |
| `opentopomap` | [Classic OpenTopoMap](https://github.com/der-stefan/OpenTopoMap/tree/60c50cb8329d67c8556cd9f25b4a8e50bfc19c91/mapnik) | `60c50cb8329d67c8556cd9f25b4a8e50bfc19c91` |
| `humanitarian` | [HOT HDM-CartoCSS](https://github.com/hotosm/HDM-CartoCSS/tree/ff2bb32dc96e5e450dab052be0df0dff40f1c139) | `ff2bb32dc96e5e450dab052be0df0dff40f1c139` |

## Requirements

- Built `build/out/mapnik-ground-truth-render` and its GeoJSON/GDAL input plugins.
- Python with `lxml`, `PyYAML`, `psycopg2`, and GDAL Python bindings. On this
  workstation use `/usr/bin/python3`; the RDF environment is `.venv/bin/python`
  with `rdflib` and optionally `pyshacl`.
- Docker, Git, a C compiler, GDAL development headers, and DejaVu fonts.
- An existing PostGIS OSM import: `planet_osm_point`, `planet_osm_line`,
  `planet_osm_polygon`, `planet_osm_roads`, each with `osm_id`, `way` (EPSG:3857),
  and hstore `tags`; imported OSM coastline `water_polygons.way` in EPSG:3857.
  The import must cover the requested extent plus its 256-pixel buffer.

## Run

From the repository root:

```sh
/usr/bin/python3 demo/ground_truth/map-products/setup.py
/usr/bin/python3 demo/ground_truth/map-products/render.py \
  --product both --center 11.076 49.457 --zoom 15 \
  --output build/map-products/nuernberg
```

Use `--product opentopomap` or `--product humanitarian` for one product.
`--center` is **longitude latitude**. Supported zooms are 12–17; `--size` sets a
square image, default 768 pixels. Database defaults are host `127.0.0.1`, port
`54321`, database `gis`, user `postgres`; override with the corresponding flags.
Use PostgreSQL's `.pgpass`/`PGPASSWORD` for authentication. The database must
already be running; these scripts do not start or replace an import.

Setup downloads the pinned styles and builds a CartoCSS 1.2.0 Docker image.
OpenTopoMap downloads cached [Mapzen elevation tiles](https://registry.opendata.aws/terrain-tiles/),
then derives 10-metre contours, hillshade and relief. This resolution describes
processing, not a guarantee of elevation accuracy. Humanitarian retains its
upstream-disabled terrain layers.

Open `index.html` in the output directory. Each product includes `map.png`,
`map.json`, `map.ttl`, `style.xml`, materialized layer GeoJSON, and `manifest.json`.
The RDF contains the document-relative `cg:imageUrl <map.png>` link. Rendering
from the prepared XML needs no database, but still needs the upstream assets,
fonts, and terrain at the paths recorded in the XML.

## Identity, provenance and limits

Preparation uses session-local `pg_temp` tables/functions; persistent OSM tables
are only read. Upstream SQL receives `osm_id` and `osm_type` through nested
queries and UNION branches. Unmapped aggregations and unsupported datasources
fail explicitly. Manifests record active, empty and disabled layers, source
revisions, counts, and final identity checks. GDAL raster identities are added
after rendering from the raster's SHA-256; contours and coastlines use derived
source IDs, without invented OSM IDs.

This is regional rendering, not a synchronized replica of either public tile
service. Upstream HDM-CartoCSS is archived. Peak isolation uses upstream DEM
code with a 6-km search cap, sufficient for the supported zoom thresholds;
nearby peaks, hiking-parking classification and regional label placement depend
on snapshot coverage. Mapnik logs warnings for some upstream SVG attributes and
its built-in ellipse marker. Ground-truth visibility
uses the renderer's existing pixel ownership model: hillshade blending does
not represent every underlying contributor as independently visible.

Keep attribution when sharing outputs: OpenStreetMap contributors (ODbL),
OpenTopoMap style (CC BY-SA 3.0), HOT HDM-CartoCSS (CC0), and the elevation source
attribution linked in `terrain/terrain.json`. Bundled fonts retain their licenses.

## Checks

```sh
/usr/bin/python3 -m unittest discover -s demo/ground_truth/map-products -p 'test_*.py' -v
.venv/bin/python demo/ground_truth/rdf/validate_and_query.py \
  build/map-products/nuernberg/opentopomap/map.ttl --ontology map-display-ontology
```

Repeat RDF validation for `humanitarian/map.ttl`. Image review, nonempty OSM
objects, complete source identities, and successful RDF generation are separate
checks; a successful XML compilation alone is not enough.
