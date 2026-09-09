#!/usr/bin/env python3
"""Materialize upstream Mapnik styles against a bounded OSM/PostGIS snapshot.

All database writes use session-local temporary tables, types and functions.
The resulting XML and GeoJSON render without a running database. Upstream
portrayal rules are retained; inactive layers are recorded in the manifest.
"""
import argparse
import importlib.util
import json
import math
from pathlib import Path
import re
import subprocess

from lxml import etree as ET
import psycopg2
from psycopg2 import sql
import yaml

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('query_identity', HERE.parent / 'osm-carto/patch_project_queries.py')
IDENTITY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IDENTITY)
MERCATOR = 'epsg:3857'
RADIUS = 6378137.0


def extent_for(lon, lat, zoom, size):
    resolution = 2 * math.pi * RADIUS / (256 * 2 ** zoom)
    x = math.radians(lon) * RADIUS
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * RADIUS
    half = resolution * size / 2
    return [x-half, y-half, x+half, y+half], resolution


def active(style, scale):
    return any(float(rule.findtext('MinScaleDenominator', '0')) <= scale <
               float(rule.findtext('MaxScaleDenominator', 'inf')) for rule in style.findall('Rule'))


def with_identity(query):
    # The upstream files use mixed keyword case. Only normalize SQL keywords,
    # leaving string literals and quoted identifiers intact.
    pieces = re.split(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")", query)
    for i in range(0, len(pieces), 2):
        pieces[i] = re.sub(r'\b(select|from|join|union|all|where|group|by|order|as)\b',
                           lambda m: m[0].upper(), pieces[i], flags=re.I)
    query = ''.join(pieces)
    if re.search(r'\bGROUP\s+BY\b|\b(?:ST_Collect|ST_Union|string_agg|array_agg)\s*\(', query, re.I):
        raise ValueError('Aggregated layer needs an explicit multi-feature identity mapping')

    def patch(match):
        if not IDENTITY.is_table_subquery(query, match.start()):
            return match[0]
        projection = IDENTITY.projection_list(query, match.end())
        if projection.lstrip().startswith('*'):
            return match[0]
        columns = IDENTITY.split_columns(projection)
        names = {re.split(r'\bAS\b', c, flags=re.I)[-1].strip().split('.')[-1] for c in columns}
        qualifier = IDENTITY.qualifier(query, match.end() + len(projection))
        missing = [qualifier + name for name in ('osm_id', 'osm_type') if name not in names]
        return match[0] + (', '.join(missing) + ', ' if missing else '')
    return IDENTITY.SELECT_HEAD.sub(patch, query)


def parameters(layer):
    return {p.get('name'): p.text for p in layer.findall('Datasource/Parameter')}


def datasource(layer, path, kind='geojson'):
    for node in layer.findall('Datasource'):
        layer.remove(node)
    node = ET.SubElement(layer, 'Datasource')
    for name, value in [('type', kind), ('file', str(path.resolve()))]:
        ET.SubElement(node, 'Parameter', name=name).text = value
    layer.set('srs', MERCATOR)


def snapshot(cursor, bounds, tag_style):
    tags = {}
    for line in tag_style.read_text().splitlines():
        parts = line.split('#', 1)[0].split()
        if len(parts) >= 3 and parts[2] in ('text', 'int4', 'real') and '*' not in parts[1] and 'delete' not in parts:
            tags[parts[1]] = parts[2]
    tags.update({'otm_isolation': 'text', 'network': 'text', 'healthcare': 'text',
                 'cuisine': 'text', 'wheelchair': 'text', 'exit_to': 'text'})
    counts = {}
    cursor.execute('SET search_path TO pg_temp, public')
    for table in ('planet_osm_point', 'planet_osm_line', 'planet_osm_polygon', 'planet_osm_roads'):
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (table,))
        existing = [row[0] for row in cursor.fetchall()]
        if not existing:
            raise ValueError(f'Missing OSM source table: {table}')
        expressions = []
        for name in dict.fromkeys(existing + list(tags)):
            if name in existing:
                expr = sql.SQL('p.{}').format(sql.Identifier(name))
            elif name == 'way_area':
                expr = sql.SQL('ST_Area(p.way)')
            elif name == 'z_order':
                expr = sql.SQL('0')  # node-only import field; no ordering use
            else:
                expr = sql.SQL('p.tags -> {}').format(sql.Literal(name))
            if name in tags:
                expr = sql.SQL('({})::{}').format(expr, sql.SQL(tags[name]))
            expressions.append(sql.SQL('{} AS {}').format(expr, sql.Identifier(name)))
        osm_type = "'node'" if table == 'planet_osm_point' else "CASE WHEN p.osm_id < 0 THEN 'relation' ELSE 'way' END"
        expressions.append(sql.SQL(osm_type + ' AS osm_type'))
        statement = sql.SQL('CREATE TEMP TABLE pg_temp.{} AS SELECT {} FROM public.{} p WHERE p.way && ST_MakeEnvelope(%s,%s,%s,%s,3857)').format(
            sql.Identifier(table), sql.SQL(',').join(expressions), sql.Identifier(table))
        cursor.execute(statement, bounds)
        counts[table] = cursor.rowcount
        cursor.execute(sql.SQL('CREATE INDEX ON pg_temp.{} USING gist(way)').format(sql.Identifier(table)))
        cursor.execute(sql.SQL('ANALYZE pg_temp.{}').format(sql.Identifier(table)))
    return counts


def topo_functions(cursor, upstream):
    files = ['arealabel.sql', 'pitchicon.sql', 'viewpointdirection.sql', 'stationdirection.sql']
    texts = [(upstream / 'mapnik/tools' / name).read_text() for name in files]
    names = set()
    for text in texts:
        names.update(re.findall(r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:FUNCTION|TYPE)\s+(\w+)', text, re.I))
    pattern = re.compile(r'\b(' + '|'.join(sorted(names, key=len, reverse=True)) + r')\b', re.I)
    for text in texts:
        text = re.sub(r'\bUPDATE\s+planet_osm_point\b', 'UPDATE pg_temp.planet_osm_point', text)
        cursor.execute(pattern.sub(lambda m: 'pg_temp.' + m[0], text))
    return pattern


def topo_derived(cursor):
    # Same derivation as upstream update_lowzoom.sh, restricted to the snapshot.
    cursor.execute('''CREATE TEMP VIEW naturalarealabels AS
        SELECT osm_id, osm_type, pg_temp.natural_arealabel(osm_id,way) AS way,name,
               COALESCE("natural","region:type") AS areatype,way_area,
               (pg_temp.OTM_Next_Natural_Area_Size(osm_id,way_area,way)).*
        FROM planet_osm_polygon WHERE name IS NOT NULL AND
        ("region:type" IN ('natural_area','mountain_area','mountain_range','basin') OR
         "natural" IN ('massif','mountain_range','basin','valley','couloir','ridge','arete','gorge','gully','canyon'))
        UNION ALL
        SELECT osm_id,osm_type,way,name,"natural",ST_Length(way)^2/10,
               (pg_temp.OTM_Next_Natural_Area_Size(osm_id,0.0,way)).*
        FROM planet_osm_line li WHERE name IS NOT NULL AND
         "natural" IN ('massif','mountain_range','basin','valley','couloir','ridge','arete','gorge','gully','canyon')
         AND NOT EXISTS (SELECT 1 FROM planet_osm_polygon po WHERE po.osm_id=li.osm_id)''')
    cursor.execute('''CREATE TEMP VIEW lakelabels AS
        SELECT osm_id,osm_type,pg_temp.arealabel(osm_id,way) AS way,name,way_area AS lake_area,
        CASE WHEN "natural"='glacier' THEN 'glacieraxis' WHEN "natural"='bay' THEN 'bayaxis'
             WHEN "natural"='strait' THEN 'straitaxis' ELSE 'lakeaxis' END AS label
        FROM planet_osm_polygon WHERE name IS NOT NULL AND
        ("natural" IN ('water','bay','strait','glacier') OR water='lake' OR landuse IN ('basin','reservoir'))
        UNION ALL
        SELECT osm_id,osm_type,ST_LineMerge(ST_Collect(way)),MAX(name),SUM(ST_Length(way))^2/10,'straitaxis'
        FROM planet_osm_line WHERE "natural"='strait' AND name IS NOT NULL GROUP BY osm_id,osm_type''')


def topo_elevation_attributes(cursor, upstream, terrain, output):
    """Run upstream DEM algorithms; update only this connection's snapshot."""
    counts = {}
    for tool, field, kinds in [('isolation', 'otm_isolation', ('peak', 'volcano')),
                               ('saddledirection', 'direction', ('saddle', 'col', 'notch'))]:
        cursor.execute(sql.SQL('SELECT osm_id, ST_X(ST_Transform(way,4326)), '
                              'ST_Y(ST_Transform(way,4326)), {} FROM planet_osm_point '
                              'WHERE "natural" IN %s').format(
                                  sql.Identifier('ele' if tool == 'isolation' else field)), (kinds,))
        rows = cursor.fetchall()
        counts[tool] = len(rows)
        if not rows:
            continue
        executable = output / tool
        subprocess.run(['cc', '-O2', '-o', str(executable),
                        str(upstream/'mapnik/tools'/f'{tool}.c'), '-lgdal', '-lm'], check=True,
                       capture_output=True)
        arguments = [str(executable), '-f', str((terrain/'dem-wgs84.tif').resolve()), '-o', 'csv']
        if tool == 'isolation':
            arguments += ['-r', '6000']
        result = subprocess.run(arguments, input=''.join(';'.join('' if x is None else str(x) for x in row)+'\n'
                                for row in rows), check=True, capture_output=True, text=True)
        updates = []
        for line in result.stdout.splitlines():
            values = line.split(';')
            if len(values) != 4:
                raise ValueError(f'Unexpected {tool} output: {line}')
            oid, _, _, value = values
            if int(value) < 0:
                raise ValueError(f'{tool} could not derive {field} for OSM node {oid}')
            updates.append((value, int(oid)))
        if len(updates) != len(rows):
            raise ValueError(f'{tool} did not return all input features')
        cursor.executemany(sql.SQL('UPDATE pg_temp.planet_osm_point SET {}=%s WHERE osm_id=%s').format(
            sql.Identifier(field)), updates)
    return counts


def topo_parking(cursor):
    """Upstream hiking-parking selection and nearest-parking distance, locally."""
    counts = {}
    for table in ('planet_osm_polygon', 'planet_osm_point'):
        amenities = ('hospital', 'school', 'university')
        if table == 'planet_osm_point':
            amenities += ('parking',)
        cursor.execute(sql.SQL('''UPDATE pg_temp.{} AS p SET hiking='_otm_yes'
            WHERE amenity='parking' AND (access IS NULL OR access IN ('yes','public'))
            AND (hiking IS NULL OR hiking NOT IN ('no','yes'))
            AND NOT EXISTS (SELECT 1 FROM pg_temp.planet_osm_polygon q WHERE
                (landuse IN ('industrial','commercial','retail','residential','military','cemetery','allotments','farmyard')
                 OR amenity IN %s OR leisure IN ('sports_centre','pitch') OR aeroway='aerodrome')
                AND ST_Intersects(q.way,ST_Expand(p.way,50)))''').format(sql.Identifier(table)), (amenities,))
        cursor.execute(sql.SQL('''UPDATE pg_temp.{table} AS p SET otm_isolation=(
            SELECT floor(LEAST(5000,COALESCE(MIN(ST_Distance(ST_Centroid(p.way),ST_Centroid(q.way))),5000)))::text
            FROM pg_temp.{table} q WHERE q.osm_id != p.osm_id AND q.amenity='parking'
                AND q.hiking IN ('yes','_otm_yes'))
            WHERE p.amenity='parking' AND p.hiking IN ('yes','_otm_yes')''').format(table=sql.Identifier(table)))
        counts[table] = cursor.rowcount
    return counts


def has_coordinates(geometry):
    """Empty PostGIS results have no drawable coordinates and cannot feed GeoJSON input."""
    if geometry is None:
        return False
    if geometry.get('type') == 'GeometryCollection':
        return any(has_coordinates(part) for part in geometry.get('geometries', []))

    def contains_point(coordinates):
        return bool(coordinates) and (
            isinstance(coordinates[0], (int, float)) or any(contains_point(part) for part in coordinates))

    return contains_point(geometry.get('coordinates', []))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product', choices=['opentopomap', 'humanitarian'], required=True)
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--tag-style', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--center', type=float, nargs=2, metavar=('LON', 'LAT'), required=True)
    parser.add_argument('--zoom', type=int, default=15)
    parser.add_argument('--size', type=int, default=768)
    parser.add_argument('--terrain', type=Path)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', default=54321, type=int)
    parser.add_argument('--database', default='gis')
    parser.add_argument('--user', default='postgres')
    args = parser.parse_args()
    if not 12 <= args.zoom <= 17:
        parser.error('Supported zoom range is 12–17; lower zooms require global generalization tables')
    if not 64 <= args.size <= 2048 or not -180 < args.center[0] < 180 or not -80 < args.center[1] < 80:
        parser.error('Use size 64–2048 and a center within longitude ±180, latitude ±80')
    args.output.mkdir(parents=True, exist_ok=True)
    args.output = args.output.resolve()
    args.upstream = args.upstream.resolve()
    extent, resolution = extent_for(*args.center, args.zoom, args.size)
    scale = resolution / .00028
    buffered = [extent[0]-256*resolution, extent[1]-256*resolution,
                extent[2]+256*resolution, extent[3]+256*resolution]
    if args.product == 'opentopomap':
        style_dir = args.upstream / 'mapnik'
        root = ET.parse(str(style_dir / 'opentopomap.xml'), ET.XMLParser(load_dtd=True, resolve_entities=True, no_network=True)).getroot()
        if args.terrain is None:
            parser.error('OpenTopoMap requires --terrain (prepared DEM, hillshade and contours)')
        args.terrain = args.terrain.resolve()
        terrain_metadata = json.loads((args.terrain/'terrain.json').read_text())
        coverage = terrain_metadata['bounds']
        if any(coverage[i] > buffered[i] for i in (0, 1)) or any(coverage[i] < buffered[i] for i in (2, 3)):
            parser.error('Terrain does not cover this buffered viewport; prepare it for the same center, zoom and size')
    else:
        style_dir = args.upstream
        project = yaml.safe_load((style_dir / 'project.yml').read_text())
        for layer in project['Layer']:
            ds = layer['Datasource']
            if ds.get('type') == 'postgis':
                ds.pop('password', None)
                ds.update(host=args.host, port=args.port, user=args.user, dbname=args.database)
        project_file = style_dir / 'maptrace.mml'
        project_file.write_text(json.dumps(project))
        result = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '-v', f'{style_dir}:/work',
                                 '-w', '/work', 'carto-cli:latest', 'carto', 'maptrace.mml'],
                                check=True, capture_output=True, text=True)
        root = ET.fromstring(result.stdout.encode())
    styles = {s.get('name'): s for s in root.findall('Style')}
    root.set('srs', MERCATOR)
    for node in root.iter():
        for attribute in ('file', 'font-directory'):
            value = node.get(attribute)
            if value and not Path(value).is_absolute():
                node.set(attribute, str((style_dir / value).resolve()))
    report = dict(product=args.product, revision=subprocess.check_output(['git','-C',str(args.upstream),'rev-parse','HEAD'],text=True).strip(),
                  extent=extent, zoom=args.zoom, size=args.size, center=args.center, layers=[], excluded=[])
    if args.product == 'opentopomap':
        report['terrain'] = terrain_metadata
        report['peak_isolation_search_metres'] = 6000
    with psycopg2.connect(host=args.host,port=args.port,dbname=args.database,user=args.user,
                         options='-c statement_timeout=60000') as connection:
        with connection.cursor() as cursor:
            report['snapshot'] = snapshot(cursor, buffered, args.tag_style)
            if not report['snapshot']['planet_osm_point']:
                raise ValueError('No OSM nodes in the requested area; import data before rendering')
            functions = None
            if args.product == 'opentopomap':
                functions = topo_functions(cursor, args.upstream)
                topo_derived(cursor)
                report['elevation_attributes'] = topo_elevation_attributes(cursor, args.upstream, args.terrain, args.output)
                report['hiking_parking'] = topo_parking(cursor)
            for layer in list(root.findall('Layer')):
                name = layer.get('name')
                if layer.get('status') == 'off' or not any(active(styles[s.text], scale) for s in layer.findall('StyleName')):
                    report['excluded'].append(dict(layer=name, reason='inactive at requested scale or upstream-disabled'))
                    root.remove(layer)
                    continue
                d = parameters(layer)
                if d.get('type') == 'gdal':
                    raster = args.terrain / ('hillshade.tif' if 'hillshade' in name else 'relief.tif')
                    if not raster.exists(): raise FileNotFoundError(raster)
                    datasource(layer, raster, 'gdal')
                    report['layers'].append(dict(layer=name, source='elevation raster', file=str(raster)))
                    continue
                if name == 'contours':
                    contour_file = args.terrain / 'contours.geojson'
                    if not contour_file.exists(): raise FileNotFoundError(contour_file)
                    if not json.loads(contour_file.read_text())['features']:
                        root.remove(layer)
                        report['excluded'].append(dict(layer=name, reason='no contours in terrain extent'))
                        continue
                    datasource(layer, contour_file)
                    report['layers'].append(dict(layer=name, source='elevation contours', file=str(contour_file)))
                    continue
                if d.get('type') == 'shape':
                    # Reuse the imported OSM coastline geometries; no global shapefile download.
                    cursor.execute('SELECT ST_AsGeoJSON(ST_Intersection(ST_Union(way),ST_MakeEnvelope(%s,%s,%s,%s,3857)))::json FROM public.water_polygons WHERE way && ST_MakeEnvelope(%s,%s,%s,%s,3857)',buffered*2)
                    water = cursor.fetchone()[0]
                    if args.product == 'humanitarian':
                        cursor.execute('SELECT ST_AsGeoJSON(ST_Difference(ST_MakeEnvelope(%s,%s,%s,%s,3857),COALESCE(ST_SetSRID(ST_GeomFromGeoJSON(%s),3857),ST_GeomFromText(\'POLYGON EMPTY\',3857))))::json',buffered+[json.dumps(water) if water else None])
                        geometry=cursor.fetchone()[0]
                    else: geometry=water
                    features=[] if not has_coordinates(geometry) else [dict(type='Feature',geometry=geometry,properties={'source_id':'osm-coastline:'+name})]
                elif d.get('type') == 'postgis':
                    query=with_identity(d['table'])
                    if functions: query=functions.sub(lambda m:'pg_temp.'+m[0],query)
                    query=query.replace('!bbox!',f'ST_MakeEnvelope({",".join(map(str,buffered))},3857)').replace('!scale_denominator!',str(scale)).replace('!pixel_width!',str(resolution)).replace('!pixel_height!',str(resolution))
                    try:
                        cursor.execute(f'SELECT ST_AsGeoJSON(q.way)::json, to_jsonb(q)-\'way\' FROM {query} q' if not re.search(r'\)\s+(?:AS\s+)?\w+\s*$',query,re.I) else f'SELECT ST_AsGeoJSON(q.way)::json, to_jsonb(q)-\'way\' FROM (SELECT * FROM {query}) q')
                    except Exception as error:
                        raise RuntimeError(f'{name}: {error}\nSQL: {query}') from error
                    features=[]
                    for geometry,properties in cursor.fetchall():
                        if not has_coordinates(geometry):continue
                        oid=properties.get('osm_id');kind=properties.get('osm_type')
                        if oid is None or kind not in ('node','way','relation'):
                            raise ValueError(f'{name}: feature without unambiguous OSM identity')
                        properties['source_id']=f'osm:{kind}/{abs(oid)}'
                        features.append(dict(type='Feature',geometry=geometry,properties=properties))
                else:
                    raise ValueError(f'{name}: unsupported datasource {d}')
                if not features:
                    root.remove(layer)
                    report['excluded'].append(dict(layer=name, reason='no features in buffered extent'))
                    continue
                path=args.output/(name+'.geojson')
                path.write_text(json.dumps(dict(type='FeatureCollection',features=features)))
                datasource(layer,path)
                report['layers'].append(dict(layer=name,features=len(features),source='osm'))
                print(f'{name}: {len(features)} features',flush=True)
    used={s.text for l in root.findall('Layer') for s in l.findall('StyleName')}
    for s in list(root.findall('Style')):
        if s.get('name') not in used: root.remove(s)
    (args.output/'style.xml').write_bytes(ET.tostring(root,encoding='UTF-8',xml_declaration=True,pretty_print=True))
    (args.output/'manifest.json').write_text(json.dumps(report,indent=2))
    print('Prepared',args.output/'style.xml',flush=True)


if __name__=='__main__':
    main()
