"""Fetch pinned official DTK50 rasters through ADV on the local render grid."""
import argparse
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image, ImageStat

BASE = 'https://wunderfacts.com/adv/'
SOURCES = {
    'NRW': dict(service='https://www.wms.nrw.de/geobasis/wms_nw_dtk50',
                layer='nw_dtk50_col', attribution='Geobasis NRW — official DTK50, via Linked AdV'),
    'Saxony': dict(service='https://geodienste.sachsen.de/wms_geosn_dtk-p-color/guest',
                   layer='sn_dtk50_p_color', attribution='GeoSN — official DTK50, via Linked AdV'),
}


def request_url(area):
    source = SOURCES[area['states'][0]]
    # ADV uses the supplied CRS for bbox coordinates as well as the raster.
    # Its API accepts these lowercase parameters, not a raw WMS GetMap query.
    return BASE+'wms?'+urlencode(dict(s=source['service'], layer=source['layer'],
        bbox=','.join(map(str,area['extent_3857'])), width=area['size'], height=area['size'], crs='EPSG:3857'))


def check_image(data, size):
    with Image.open(BytesIO(data)) as im:
        im.load()
        if im.format != 'PNG' or im.size != (size,size):
            raise ValueError('Reference must be PNG on the exact requested pixel grid')
        if max(ImageStat.Stat(im.convert('RGB')).var) < 1:
            raise ValueError('Blank reference response; refusing to install it')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch',type=Path)
    args=parser.parse_args()
    batch=args.batch.resolve()
    cache=batch/'reference-cache'
    cache.mkdir(exist_ok=True)
    results=[]
    last_request=0
    for area in json.loads((batch/'areas.json').read_text()):
        try:
            url=request_url(area)
            key=hashlib.sha256(url.encode()).hexdigest()
            body_path=cache/(key+'.png')
            metadata_path=cache/(key+'.json')
            if body_path.exists() and metadata_path.exists():
                data=body_path.read_bytes()
                http=json.loads(metadata_path.read_text())
                if hashlib.sha256(data).hexdigest()!=http['sha256']:
                    raise ValueError('Cached reference checksum mismatch')
            else:
                time.sleep(max(0,12-(time.monotonic()-last_request)))
                last_request=time.monotonic()
                request=Request(url,headers={'User-Agent':'Mapnik-DTK50-State-Comparison/1.0','Accept':'image/png'})
                with urlopen(request,timeout=150) as response:
                    data=response.read()
                    http=dict(url=url,final_url=response.url,status=response.status,
                        retrieved_at=datetime.now(timezone.utc).isoformat(),headers=dict(response.headers),
                        sha256=hashlib.sha256(data).hexdigest())
                check_image(data,area['size'])
                body_path.write_bytes(data)
                metadata_path.write_text(json.dumps(http,indent=2))
            check_image(data,area['size'])
            directory=batch/area['name']/'dtk50'
            reference=dict(product='official-dtk50',state=area['states'][0],
                source=SOURCES[area['states'][0]],transport='adv-wms',wrapper=BASE,
                crs='EPSG:3857',extent=area['extent_3857'],bbox_wgs84=area['bbox'],
                width=area['size'],height=area['size'],http=http,
                alignment='Requested exact local map extent and size; no local resampling, cropping, or mosaicking.',
                graph_scope='The local knowledge graph annotates map.png only, not this official raster.',
                source_date='Upstream raster edition/date not independently verified; retrieved_at is the download time.')
            (directory/'reference.png').write_bytes(data)
            (directory/'reference.json').write_text(json.dumps(reference,indent=2,ensure_ascii=False))
            result=dict(area=area['name'],status='downloaded',reference=str(directory/'reference.png'),
                        sha256=http['sha256'])
        except Exception as error:
            result=dict(area=area['name'],status='failed',error=str(error))
        results.append(result)
        print(json.dumps(result),flush=True)
        (batch/'reference-acquisition.json').write_text(json.dumps(results,indent=2))
    if any(r['status']=='failed' for r in results):
        raise SystemExit(1)


if __name__=='__main__':
    main()
