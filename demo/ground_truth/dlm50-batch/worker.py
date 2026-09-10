"""Validate one generated map/graph pair using the existing batch checks."""
import json
from pathlib import Path
import sys
from PIL import Image, ImageStat

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'demo/ground_truth/berlin-batch'))
from validate_batch import check

batch=Path(sys.argv[1]).resolve(); name=sys.argv[2]
area=next(a for a in json.loads((batch/'areas.json').read_text()) if a['name']==name)
directory=batch/name/'dtk50'
result=check((directory,area,ROOT/'map-display-ontology'))
if not result['shacl_conforms']: raise ValueError('Knowledge graph failed SHACL')
with Image.open(directory/'map.png') as image:
    if max(ImageStat.Stat(image.convert('RGB')).var)<1: raise ValueError('Blank map')
truth=json.loads((directory/'map.json').read_text())
if len(truth['objects'])<20 or len(truth['source_geometries'])<20:
    raise ValueError('Unexpectedly empty map or input geometry')
if not truth['source_snapshot']['road_dependency_acquisition']['complete']:
    raise ValueError('Incomplete road acquisition')
result.update(nonblank_image=True,road_dependencies_complete=True,
              source_features=len(truth['source_geometries']),preview=truth['coverage']['preview'])
(directory/'validation.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
