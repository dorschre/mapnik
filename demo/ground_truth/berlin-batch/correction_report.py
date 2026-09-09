"""Summarize verified DTK50 batch corrections against preserved prior renders."""
import argparse
import json
from pathlib import Path


def metrics(path):
    truth=json.loads(path.read_text())
    types={name:set() for name in ('AX_Strassenachse','AX_Hoehenlinie')}
    points=0
    forbidden=0
    for obj in truth['objects']:
        props=obj['source_properties']
        if props.get('geometry_type')=='point':
            points+=1
            forbidden+=props.get('rule_id') in ('RUL02070','RUL02130','RUL05461','RUL00055')
        name=props.get('feature_type')
        if name in types and props.get('geometry_type')=='curve':
            types[name].update(json.loads(props.get('source_uris','[]')))
    return dict(visible_road_features=len(types['AX_Strassenachse']),
                visible_contour_features=len(types['AX_Hoehenlinie']),
                point_symbol_objects=points,presentation_only_point_examples=forbidden)


def main(batch):
    rows=[]
    for area in json.loads((batch/'areas.json').read_text()):
        name=area['name']
        before=metrics(batch/'before-catalog-fix'/name/'dtk50/map.json')
        after=metrics(batch/name/'dtk50/map.json')
        rows.append(dict(area=name,before=before,after=after))
    by_name={r['area']:r for r in rows}
    assert by_name['wermsdorf']['after']['visible_road_features']>0
    assert by_name['wermsdorf']['after']['visible_contour_features']>0
    assert by_name['cologne']['after']['presentation_only_point_examples']==0
    report=dict(causes=[
        'Road axes lacked referenced AX_Strasse records needed for classification; 64 Wermsdorf parents retrieved from ADV.',
        'Contour rules received terrain_zone=None, so all terrain-dependent line rules evaluated false.',
        'presentation_mode=all emitted symbols reserved for separate cartographic presentation objects.'],
        changes=['Use stdpraes with include_ap=False on these DLM50 inputs.',
                 'Supply documented regional terrain categories to Saxony contour rules.',
                 'Use the separately archived enriched Wermsdorf snapshot with original ADV parent IDs.'],
        limitations=['Other areas still have unresolved road parents.',
                     'Official presentation placement, label placement, and symbol generalization are not reconstructed.',
                     'Unsupported relation/filter rules remain; outputs are still previews.'],maps=rows)
    (batch/'corrections.json').write_text(json.dumps(report,indent=2))
    text='# DTK50 rendering corrections\n\n'
    for name in ('wermsdorf','cologne'):
        row=by_name[name]
        text+=f'## {name.title()}\n\n'
        for key,value in row['after'].items():
            text+=f'- {key.replace("_"," ")}: {row["before"][key]} → {value}\n'
        text+='\n'
    text+='## Causes and changes\n\n'+'\n'.join('- '+s for s in report['causes']+report['changes'])
    text+='\n\n## Remaining limitations\n\n'+'\n'.join('- '+s for s in report['limitations'])+'\n'
    text+='\nTerrain context reference: [Sachsenforst, Horstsee Wermsdorf](https://www.wald.sachsen.de/54_Horstsee_Wermsdorf.pdf).\n'
    (batch/'CORRECTIONS.md').write_text(text)
    print(json.dumps([by_name['wermsdorf'],by_name['cologne']],indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('batch',type=Path)
    main(p.parse_args().batch.resolve())
