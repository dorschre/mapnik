"""Select 25 diverse, buffered DTK50 views within each of four ADV states."""
import json
from pathlib import Path
import sys
from collections import Counter
from pyproj import Transformer
from shapely.geometry import shape, box
from shapely.ops import transform

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'demo/ground_truth/berlin-batch'))
from prepare_areas import align

# Approximate named-area centers; exact aligned extents are recorded in areas.json.
# f=lowland, h=hilly, m=low mountain range: explicit regional assumptions.
CENTERS={
'NRW': '''
cologne|Cologne|6.96|50.94|f
bonn|Bonn|7.10|50.735|h
muenster|Münster|7.625|51.96|f
bad-muenstereifel|Bad Münstereifel|6.765|50.555|m
telgte|Telgte|7.785|51.985|f
aachen|Aachen|6.10|50.78|h
duesseldorf|Düsseldorf|6.78|51.23|f
dortmund|Dortmund|7.465|51.515|h
essen|Essen|7.01|51.455|h
duisburg|Duisburg|6.765|51.435|f
bochum|Bochum|7.22|51.48|h
wuppertal|Wuppertal|7.17|51.255|h
bielefeld|Bielefeld|8.535|52.02|h
paderborn|Paderborn|8.755|51.72|h
detmold|Detmold|8.88|51.935|h
siegen|Siegen|8.025|50.875|m
soest|Soest|8.11|51.575|f
arnsberg|Arnsberg|8.085|51.395|m
winterberg|Winterberg|8.525|51.195|m
monschau|Monschau|6.28|50.555|m
xanten|Xanten|6.455|51.66|f
haltern|Haltern am See|7.185|51.745|f
emsdetten|Emsdetten|7.53|52.17|f
hoexter-west|Höxter west|9.34|51.78|h
schleiden|Schleiden|6.50|50.53|m
''',
'Saxony': '''
dresden|Dresden|13.737|51.05|h
meissen|Meissen|13.475|51.164|h
freiberg|Freiberg|13.343|50.917|m
bad-schandau|Bad Schandau|14.153|50.918|m
wermsdorf|Wermsdorf|12.94|51.28|h
leipzig|Leipzig|12.375|51.34|f
chemnitz|Chemnitz|12.925|50.835|h
zwickau|Zwickau|12.495|50.72|h
plauen|Plauen|12.145|50.50|h
bautzen|Bautzen|14.435|51.18|h
goerlitz-west|Görlitz west|14.94|51.155|h
zittau-west|Zittau west|14.775|50.915|h
torgau|Torgau|13.0|51.56|f
riesa|Riesa|13.295|51.305|f
doebeln|Döbeln|13.115|51.12|h
grimma|Grimma|12.725|51.24|h
borna|Borna|12.495|51.125|f
delitzsch|Delitzsch|12.34|51.525|f
eilenburg|Eilenburg|12.635|51.465|f
kamenz|Kamenz|14.10|51.27|h
hoyerswerda|Hoyerswerda|14.245|51.435|f
annaberg-buchholz|Annaberg-Buchholz|13.0|50.58|m
marienberg|Marienberg|13.16|50.65|m
aue|Aue|12.70|50.59|m
olbernhau|Olbernhau|13.335|50.665|m
''',
'Hesse': '''
frankfurt|Frankfurt am Main|8.68|50.115|f
wiesbaden|Wiesbaden|8.23|50.085|h
darmstadt|Darmstadt|8.655|49.875|f
kassel|Kassel|9.549|51.315|h
offenbach|Offenbach|8.775|50.105|f
hanau|Hanau|8.925|50.135|f
marburg|Marburg|8.77|50.81|h
giessen|Giessen|8.675|50.59|h
wetzlar|Wetzlar|8.505|50.555|h
fulda|Fulda|9.685|50.555|h
bad-hersfeld|Bad Hersfeld|9.71|50.87|h
eschwege|Eschwege|10.055|51.185|h
fritzlar|Fritzlar|9.275|51.135|h
korbach|Korbach|8.875|51.275|h
frankenberg|Frankenberg|8.795|51.06|h
alsfeld|Alsfeld|9.275|50.75|h
limburg-east|Limburg an der Lahn east|8.105|50.40|h
bad-homburg|Bad Homburg|8.615|50.23|h
bad-nauheim|Bad Nauheim|8.735|50.365|h
gelnhausen|Gelnhausen|9.185|50.205|h
michelstadt|Michelstadt|9.005|49.675|h
wasserkuppe|Wasserkuppe, Rhön|9.945|50.505|m
edersee|Edersee|9.055|51.185|m
hoherodskopf|Hoherodskopf, Vogelsberg|9.225|50.505|m
grosser-feldberg|Großer Feldberg, Taunus|8.465|50.23|m
''',
'Rhineland-Palatinate': '''
koblenz|Koblenz|7.595|50.355|h
mainz-west|Mainz west|8.22|50.00|h
trier|Trier|6.645|49.755|h
kaiserslautern|Kaiserslautern|7.765|49.445|h
ludwigshafen-west|Ludwigshafen west|8.385|49.48|f
speyer-west|Speyer west|8.40|49.32|f
worms-west|Worms west|8.305|49.635|f
neustadt-weinstrasse|Neustadt an der Weinstraße|8.135|49.355|h
landau|Landau|8.105|49.20|f
idar-oberstein|Idar-Oberstein|7.315|49.705|h
bad-kreuznach|Bad Kreuznach|7.865|49.845|h
andernach|Andernach|7.405|50.43|h
mayen|Mayen|7.225|50.325|h
neuwied|Neuwied|7.475|50.445|f
bad-neuenahr|Bad Neuenahr|7.115|50.545|h
altenkirchen|Altenkirchen|7.645|50.69|h
montabaur|Montabaur|7.835|50.435|h
daun|Daun|6.835|50.195|m
wittlich|Wittlich|6.895|49.985|h
bernkastel-kues|Bernkastel-Kues|7.075|49.915|h
cochem|Cochem|7.165|50.145|h
simmern|Simmern|7.525|49.98|h
birkenfeld|Birkenfeld|7.165|49.65|h
pirmasens|Pirmasens|7.605|49.20|h
bitburg|Bitburg|6.525|49.975|h
'''}


def prepare(batch):
    boundaries=json.loads((batch/'state-boundaries.geojson').read_text())
    codes={'NRW':'DEA','Saxony':'DED','Hesse':'DE7','Rhineland-Palatinate':'DEB'}
    projection=Transformer.from_crs(4326,25832,always_xy=True)
    regions={state:transform(projection.transform,shape(next(f['geometry'] for f in boundaries['features']
                     if f['properties']['NUTS_ID']==code))) for state,code in codes.items()}
    previous=ROOT/'build/dtk50-acquisition-fixed/areas.json'
    existing={a['name']:a for a in json.loads(previous.read_text())} if previous.exists() else {}
    areas=[];failures=[]
    for state,lines in CENTERS.items():
        for line in lines.strip().splitlines():
            name,label,lon,lat,terrain=line.split('|')
            area=align(dict(name=name,label=label,lon=float(lon),lat=float(lat),states=[state]),zoom=14,size=640)
            if name in existing:
                area=existing[name].copy();area.pop('snapshot_directory',None)
            bounds=projection.transform_bounds(*area['bbox'])
            buffered=box(bounds[0]-250,bounds[1]-250,bounds[2]+250,bounds[3]+250)
            if not regions[state].covers(buffered):
                failures.append(dict(area=name,outside_m2=buffered.difference(regions[state]).area))
            area.update(terrain_zone={'f':'flachland','h':'huegelland','m':'mittelgebirge'}[terrain],
                        terrain_note='Approximate area-level regional relief selection; not verified per feature.',
                        terrain_assumption=True,boundary_check='Eurostat GISCO NUTS 2024 1:1M, full acquisition bbox with 250m buffer')
            areas.append(area)
    if failures: raise ValueError(f'Cross-border views: {failures}')
    assert Counter(a['states'][0] for a in areas)==dict.fromkeys(codes,25)
    assert len({a['name'] for a in areas})==100
    # Round robin exposes early results from every state and avoids finishing
    # one entire provider before discovering a problem in another.
    areas=[areas[j*25+i] for i in range(25) for j in range(4)]
    (batch/'areas.json').write_text(json.dumps(areas,ensure_ascii=False,indent=2))
    print('100 buffered provider-contained areas prepared.')

if __name__=='__main__': prepare(Path(sys.argv[1]).resolve())
