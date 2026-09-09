#!/usr/bin/env python3
"""Prepare real Mapzen elevation, OpenTopoMap hillshade, relief and contours."""
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import shutil
import urllib.request

from osgeo import gdal, ogr, osr
from prepare import extent_for

gdal.UseExceptions()


def geographic(x, y):
    return math.degrees(x / 6378137), math.degrees(2 * math.atan(math.exp(y / 6378137)) - math.pi / 2)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--center', type=float, nargs=2, required=True)
    p.add_argument('--zoom', type=int, default=15)
    p.add_argument('--size', type=int, default=768)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--upstream', type=Path, required=True)
    a = p.parse_args()
    if not 12 <= a.zoom <= 17 or not 64 <= a.size <= 2048:
        p.error('Use zoom 12–17 and size 64–2048')
    if not -180 < a.center[0] < 180 or not -80 < a.center[1] < 80:
        p.error('Center must be within longitude ±180 and latitude ±80')
    a.output.mkdir(parents=True, exist_ok=True)
    a.cache.mkdir(parents=True, exist_ok=True)
    bounds, resolution = extent_for(*a.center, a.zoom, a.size)
    margin = 256 * resolution + 300
    bounds = [bounds[0]-margin, bounds[1]-margin, bounds[2]+margin, bounds[3]+margin]
    # Ten kilometres of additional DEM context supports bounded peak isolation.
    context = 12000 / math.cos(math.radians(abs(a.center[1]) + 1))
    west, south = geographic(bounds[0]-context, bounds[1]-context)
    east, north = geographic(bounds[2]+context, bounds[3]+context)
    if west < -180 or east > 180 or south < -85 or north > 85:
        p.error('The buffered DEM extent must not cross the antimeridian or polar limit')
    files, sources = [], []
    for lat in range(math.floor(south), math.floor(north)+1):
        for lon in range(math.floor(west), math.floor(east)+1):
            row = f'{"N" if lat >= 0 else "S"}{abs(lat):02}'
            tile = f'{row}{"E" if lon >= 0 else "W"}{abs(lon):03}'
            url = f'https://s3.amazonaws.com/elevation-tiles-prod/skadi/{row}/{tile}.hgt.gz'
            target = a.cache / (tile + '.hgt')
            if not target.exists():
                print('Downloading', tile, flush=True)
                compressed = a.cache / (tile + '.hgt.gz.part')
                with urllib.request.urlopen(url, timeout=120) as response, compressed.open('wb') as out:
                    shutil.copyfileobj(response, out)
                temporary = target.with_suffix('.hgt.part')
                with gzip.open(compressed, 'rb') as source, temporary.open('wb') as out:
                    shutil.copyfileobj(source, out)
                temporary.replace(target)
                compressed.unlink()
            files.append(str(target.resolve()))
            sources.append(dict(url=url, sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
    vrt = gdal.BuildVRT('', files)
    gdal.Warp(str(a.output/'dem-wgs84.tif'), vrt, dstSRS='EPSG:4326',
              outputBounds=[west,south,east,north], outputType=gdal.GDT_Int16,
              creationOptions=['COMPRESS=DEFLATE'])
    dem = gdal.Warp(str(a.output/'dem.tif'), vrt, dstSRS='EPSG:3857', outputBounds=bounds,
                    xRes=30, yRes=30, resampleAlg='bilinear', dstNodata=-32768,
                    creationOptions=['COMPRESS=DEFLATE'])
    gdal.DEMProcessing(str(a.output/'hillshade.tif'), dem, 'hillshade', zFactor=2,
                       computeEdges=True, creationOptions=['COMPRESS=DEFLATE'])
    gdal.DEMProcessing(str(a.output/'relief.tif'), dem, 'color-relief',
                       colorFilename=str(a.upstream/'mapnik/relief_color_text_file.txt'),
                       addAlpha=True, creationOptions=['COMPRESS=DEFLATE'])
    memory = ogr.GetDriverByName('Memory').CreateDataSource('contours')
    srs = osr.SpatialReference(); srs.ImportFromEPSG(3857)
    layer = memory.CreateLayer('contours', srs, ogr.wkbLineString)
    layer.CreateField(ogr.FieldDefn('ele', ogr.OFTReal))
    gdal.ContourGenerate(dem.GetRasterBand(1), 10, 0, [], 1, -32768, layer, -1, 0)
    features = []
    for feature in layer:
        geometry = json.loads(feature.GetGeometryRef().ExportToJson())
        key = hashlib.sha256(json.dumps(geometry, sort_keys=True).encode()).hexdigest()[:20]
        features.append(dict(type='Feature', geometry=geometry,
                             properties=dict(ele=feature.GetField('ele'), source_id='dem:contour:'+key)))
    (a.output/'contours.geojson').write_text(json.dumps(dict(type='FeatureCollection', features=features)))
    (a.output/'terrain.json').write_text(json.dumps(dict(sources=sources, bounds=bounds,
        contour_interval_metres=10, projected_resolution_metres=30,
        attribution='Mapzen terrain tiles; see https://github.com/tilezen/joerd/blob/master/docs/attribution.md'), indent=2))
    print(f'Prepared terrain: {len(features)} contour segments', flush=True)


if __name__ == '__main__':
    main()
