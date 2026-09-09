"""Portable image links in the RDF CLI output."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rdflib import Graph, URIRef

from ground_truth_to_rdf import CG

SCRIPT = Path(__file__).with_name("ground_truth_to_rdf.py")
TRUTH = {"map": {"width": 64, "height": 64, "crs": "epsg:3857",
                 "extent": [0, 0, 100, 100]}, "elements": [], "objects": []}


class ImageLinkTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def convert(self, source="area01.json", output="area01.ttl", *options):
        source = self.root / source
        output = self.root / output
        source.parent.mkdir(parents=True, exist_ok=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(json.dumps(TRUTH))
        subprocess.run([sys.executable, str(SCRIPT), str(source), "-o", str(output), *options],
                       check=True, capture_output=True, text=True)
        return output

    def test_sibling_image_remains_relative_when_document_moves(self):
        for mode in ("fragment", "path", "none"):
            with self.subTest(mode=mode):
                output = self.convert("area01.json", "area01.ttl", "--relative", mode)
                turtle = output.read_text()
                self.assertIn("cg:imageUrl <area01.png>", turtle)
                self.assertNotIn("@base", turtle)
                for base in ("https://pod.example/maps/area01.ttl", "https://other.example/moved/area01.ttl"):
                    graph = Graph().parse(data=turtle, format="turtle", publicID=base)
                    self.assertEqual(list(graph.objects(None, CG.imageUrl)),
                                     [URIRef(base.replace(".ttl", ".png"))])

    def test_separate_output_directory_and_escaped_filename(self):
        output = self.convert("images/map #ä.json", "rdf/renamed.ttl")
        turtle = output.read_text()
        self.assertIn("<../images/map%20%23%C3%A4.png>", turtle)
        graph = Graph().parse(data=turtle, format="turtle", publicID="https://pod.example/rdf/renamed.ttl")
        self.assertEqual(list(graph.objects(None, CG.imageUrl)),
                         [URIRef("https://pod.example/images/map%20%23%C3%A4.png")])

    def test_explicit_image_url(self):
        for value in ("../images/map.webp", "https://images.example/map.png"):
            with self.subTest(value=value):
                output = self.convert("area01.json", "area01.ttl", "--image-url", value)
                self.assertIn("cg:imageUrl <{}>".format(value), output.read_text())

    def test_ntriples_uses_absolute_image_iri(self):
        output = self.convert("area01.json", "area01.nt", "--format", "nt")
        graph = Graph().parse(output, format="nt")
        self.assertEqual(list(graph.objects(None, CG.imageUrl)),
                         [URIRef((self.root / "area01.png").as_uri())])

class SourceProvenanceTests(unittest.TestCase):
    def test_original_iris_and_geometry_survive_merged_feature(self):
        from ground_truth_to_rdf import Converter, PROV, GEO
        sources = ['https://provider.example/feature/1', 'https://provider.example/feature/2']
        truth = dict(TRUTH, coverage={'complete': False}, source_geometries={
            sources[0]: {'crs': 'EPSG:25833', 'wkt': 'POINT (390000 5820000)'}})
        converter = Converter(truth, 'https://example.test/data#', image_url='map.png')
        converter.emit_provenance()
        converter.emit_map()
        converter.emit_feature({'identity': {'layer': 'road', 'feature_id': 1},
                                'source_properties': {'source_uris': json.dumps(sources)}})
        graph = converter.graph
        self.assertEqual(set(graph.objects(converter.map_uri, PROV.wasDerivedFrom)), set(map(URIRef, sources)))
        geometry = next(graph.objects(URIRef(sources[0]), GEO.hasGeometry))
        self.assertEqual(str(next(graph.objects(geometry, GEO.asWKT))),
                         '<http://www.opengis.net/def/crs/EPSG/0/25833> POINT (390000 5820000)')


if __name__ == "__main__":
    unittest.main()
