#!/bin/sh
# Build a real openstreetmap-carto rendering stack and produce visual ground
# truth from it. Everything heavy runs in containers; nothing is installed on
# the host except the fonts, which land in this directory.
#
#   sh demo/ground_truth/osm-carto/setup.sh [work-dir] [region-pbf-url]
#
# Takes roughly an hour and about 6 GB, most of it the water-polygons shapefile
# that openstreetmap-carto imports for coastlines.
set -eu

WORK="${1:-$HOME/.cache/mapnik-ground-truth/osm-carto}"
PBF_URL="${2:-https://download.geofabrik.de/europe/germany/berlin-latest.osm.pbf}"
REPO="$WORK/openstreetmap-carto"

mkdir -p "$WORK"
cd "$WORK"

# 1. The stylesheet, and the data to render.
[ -d "$REPO" ] || git clone --depth 1 https://github.com/gravitystorm/openstreetmap-carto.git
[ -f "$REPO/data.osm.pbf" ] || curl -SL -o "$REPO/data.osm.pbf" "$PBF_URL"

# 2. Expose the database to the host, so our own mapnik can read it. The
#    upstream compose file keeps it on an internal network only.
cat > "$REPO/docker-compose.override.yml" <<'OVERRIDE'
services:
  db:
    ports:
      - "127.0.0.1:54321:5432"
OVERRIDE

# 3. osm2pgsql import plus the external shapefiles (coastlines, ice sheets).
cd "$REPO"
OSM2PGSQL_CACHE=4096 OSM2PGSQL_NUMPROC=8 PG_WORK_MEM=256MB PG_MAINTENANCE_WORK_MEM=2GB \
  EXTERNAL_DATA_SCRIPT_FLAGS="-C" docker compose up import

# 4. Point the stylesheet at the exposed database, and give its queries an
#    osm_id so the ground truth can name the OSM object each element depicts.
#    Upstream selects no identifier at all, so without this every visible
#    object is anonymous. All 79 datasource queries take one except
#    `junctions`, which merges several OSM nodes into a single label and so has
#    no single id to give.
HERE="$(cd "$(dirname "$0")" && pwd)"
python3 "$HERE/patch_project.py" project.mml
python3 "$HERE/patch_project_queries.py" project.mml

# 5. The Noto fonts the stylesheet asks for.
FONTDIR="$WORK/fonts" python3 scripts/get-fonts.py

# 6. CartoCSS -> Mapnik XML.
docker run --rm -v "$PWD":/w -w /w node:20-slim \
  sh -c "npm i -g -s carto@1.2.0 >/dev/null 2>&1 && carto project.mml" > osm-carto.xml

echo
echo "Ready. Render with:"
echo "  ./build/out/mapnik-ground-truth-render $REPO/osm-carto.xml <prefix> \\"
echo "    --size 699 536 --extent-wgs84 13.3745 52.5145 13.3820 52.5180 \\"
echo "    --plugins build/out/plugins/input --fonts $WORK/fonts \\"
echo "    --identity osm_id --simplify 1.0 --candidates"
echo
echo "Add --require-identity osm_id to fail the run if any visible object"
echo "cannot be linked back to OSM."
echo
echo "Stop the database with: (cd $REPO && docker compose stop db)"
