"""Validate each map/JSON/RDF pair and run the CartoGraph SHACL shapes."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path

from PIL import Image
from pyshacl import validate
from rdflib import Graph, Namespace, RDF

CG = Namespace("https://w3id.org/cartograph#")


def check(task):
    directory, area, ontology_path = task
    directory = Path(directory)
    truth = json.loads((directory / "map.json").read_text())
    assert truth["objects"], "No visible objects"
    assert Image.open(directory / "map.png").size == (area["size"], area["size"])
    assert max(abs(a - b) for a, b in zip(truth["map"]["extent"], area["extent_3857"])) < 1e-6
    graph = Graph().parse(directory / "map.ttl")
    images = list(graph.subjects(RDF.type, CG.MapImage))
    assert len(images) == 1
    assert str(graph.value(images[0], CG.imageUrl)) == (directory / "map.png").resolve().as_uri()
    assert len(set(graph.subjects(RDF.type, CG.VisibleObject))) == len(truth["objects"])
    assert graph.value(images[0], CG.viewportWkt) is not None
    if directory.name == "osm-standard":
        assert all("osm_id" in o["identity"] for o in truth["objects"])
    elif directory.name in ("humanitarian", "opentopomap"):
        assert all("source_id" in o["identity"] for o in truth["objects"])
    else:
        assert truth["coverage"]["preview"] == (not truth["coverage"]["complete"])
        assert truth["source_geometries"]
        assert any(graph.triples((None, Namespace("http://www.w3.org/ns/prov#").wasDerivedFrom, None)))
    ontology = Graph()
    for name in ("cartograph.ttl", "cartograph-provenance.ttl"):
        ontology.parse(Path(ontology_path) / name)
    shapes = Graph().parse(Path(ontology_path) / "cartograph-shapes.ttl")
    conforms, _, report = validate(graph, shacl_graph=shapes, ont_graph=ontology, inference="none", advanced=True)
    (directory / "shacl-report.txt").write_text(report)
    result = dict(area=area["name"], product=directory.name, triples=len(graph),
                  visible_objects=len(truth["objects"]), displayed_elements=len(truth["elements"]),
                  shacl_conforms=bool(conforms), checks="image dimensions, shared extent, image link, viewport, object counts, source identities/provenance",
                  inference="none")
    (directory / "validation.json").write_text(json.dumps(result, indent=2))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("batch", type=Path)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--products", nargs='+', default=['dtk50', 'osm-standard', 'opentopomap', 'humanitarian'])
    a = p.parse_args()
    root = Path(__file__).resolve().parents[3]
    tasks = [(a.batch.resolve() / area["name"] / product, area, root / "map-display-ontology")
             for area in json.loads((a.batch / "areas.json").read_text())
             for product in a.products]
    results = []
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        futures = {pool.submit(check, task): task for task in tasks}
        for future in as_completed(futures):
            task = futures[future]
            try:
                result = future.result()
            except Exception as error:
                result = dict(area=task[1]["name"], product=task[0].name, error=str(error), shacl_conforms=False)
            results.append(result)
            print(json.dumps(result), flush=True)
            (a.batch / "validation.json").write_text(json.dumps(results, indent=2))
    if not all(result["shacl_conforms"] for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
