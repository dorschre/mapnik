"""Acquire ten DLM50 windows from ADV for an NRW/Saxony comparison.

DLM50 uses global geometry pagination, unlike the live WFS per-type pagination.
The two dumps currently share the registry region `nrw`; object prefixes are
checked against the requested state. Raw HTTP responses are retained in cache.
"""
import argparse
import json
import logging
from pathlib import Path


from fetch_adv import BASE, Client
from _adv_adapter import load

acquire_dlm = load("acquisition").acquire_dlm
from prepare_areas import align

CENTERS = [
    ('cologne', 'Cologne', 6.96, 50.94, 'NRW', 'urban'),
    ('dresden', 'Dresden', 13.737, 51.05, 'Saxony', 'urban'),
    ('bonn', 'Bonn', 7.10, 50.735, 'NRW', 'river-city'),
    ('meissen', 'Meissen', 13.475, 51.164, 'Saxony', 'river-city'),
    ('muenster', 'Münster', 7.625, 51.96, 'NRW', 'mixed-settlement'),
    ('freiberg', 'Freiberg', 13.343, 50.917, 'Saxony', 'mixed-settlement'),
    ('bad-muenstereifel', 'Bad Münstereifel', 6.765, 50.555, 'NRW', 'wooded-hills'),
    ('bad-schandau', 'Bad Schandau', 14.153, 50.918, 'Saxony', 'wooded-hills'),
    ('telgte', 'Telgte', 7.785, 51.985, 'NRW', 'rural'),
    ('wermsdorf', 'Wermsdorf', 12.94, 51.28, 'Saxony', 'rural'),
]
TERRAIN = {'dresden':'huegelland','meissen':'huegelland','freiberg':'mittelgebirge',
           'bad-schandau':'mittelgebirge','wermsdorf':'huegelland'}




def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch', type=Path)
    args = parser.parse_args()
    args.batch.mkdir(parents=True, exist_ok=True)
    areas = [align(dict(name=n,label=l,lon=x,lat=y,states=[s],comparison_group=g),zoom=14,size=640)
             for n,l,x,y,s,g in CENTERS]
    for area in areas:
        if area['name'] in TERRAIN:
            area.update(terrain_zone=TERRAIN[area['name']],
                terrain_source='https://www.natur.sachsen.de/landschaftsokologische-charakterisierung-von-30-naturraumen-23087.html',
                terrain_note='Area-level regional terrain classification for contour rule selection; not a per-feature terrain survey.')
        if area['name']=='wermsdorf':
            area['terrain_source']='https://www.wald.sachsen.de/54_Horstsee_Wermsdorf.pdf'
    (args.batch / 'areas.json').write_text(json.dumps(areas, indent=2))
    output = args.batch / 'adv-snapshots'
    output.mkdir(exist_ok=True)
    client = Client(output / 'request-cache')
    status = client.session.get(BASE+'status.json', timeout=150)
    status.raise_for_status()
    (output / 'status.json').write_text(json.dumps(status.json(), indent=2))
    # rdflib logs the known missing XML namespace during RDF parsing; raw literals
    # are preserved and the adapter separately validates repaired GML and WKT.
    logging.getLogger('rdflib.term').setLevel(logging.CRITICAL)
    results = []
    for area in areas:
        try:
            result = acquire_dlm(area, output, client)
        except Exception as error:
            result = dict(area=area['name'], status='failed', error=str(error))
        results.append(result)
        (output / 'acquisition-report.json').write_text(json.dumps(results, indent=2))
        print(json.dumps(result), flush=True)
    if any(r['status']=='failed' for r in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
