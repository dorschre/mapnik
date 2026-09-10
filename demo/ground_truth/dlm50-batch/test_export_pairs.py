import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from rdflib import Graph, URIRef

spec = importlib.util.spec_from_file_location("export_pairs", Path(__file__).with_name("export_pairs.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ExportTests(unittest.TestCase):
    def test_portable_reference_and_resume(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "batch" / "cologne" / "dtk50"
            source.mkdir(parents=True)
            original = '@prefix cg: <https://w3id.org/cartograph#> .\n<map> cg:imageUrl <map.png> ; cg:label "map.png" .\n'
            (source / "map.ttl").write_text(original)
            (source / "map.png").write_bytes(b"image fixture")
            (root / "batch" / "progress.json").write_text(json.dumps({"areas": [
                {"name": "cologne", "status": "ready"},
                {"name": "dresden", "status": "rendering"},
            ]}))
            output = root / "pairs"
            module.export(root / "batch", output, {})
            graph_path = output / "cologne-map.ttl"
            graph = Graph().parse(graph_path)
            self.assertEqual(list(graph.objects(None, URIRef("https://w3id.org/cartograph#imageUrl"))),
                             [URIRef((output / "cologne-map.png").as_uri())])
            self.assertIn('"map.png"', graph_path.read_text())
            self.assertEqual((source / "map.ttl").read_text(), original)
            self.assertEqual(len(list(output.iterdir())), 2)
            stamp = graph_path.stat().st_mtime_ns
            module.export(root / "batch", output, {})
            self.assertEqual(graph_path.stat().st_mtime_ns, stamp)

    def test_missing_reference_rejected(self):
        with self.assertRaises(ValueError):
            module.rename_image("<map> <label> <map.png> .", "cologne-map.png")

    def test_maptrace_final_graph(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "tile-1"
            source.mkdir()
            (root / "progress.json").write_text('{"completed": 1, "errors": []}')
            (root / "dataset.json").write_text('{"items": [{"slug": "tile-1"}]}')
            (source / "example.json").write_text('{}')
            (source / "validation.json").write_text('[{"stage": "G_F", "conforms": true}]')
            (source / "map.png").write_bytes(b"image fixture")
            (source / "g_f.ttl").write_text('@prefix cg: <https://w3id.org/cartograph#> . <map> cg:imageUrl <map.png> .')
            output = root / "pairs"
            module.export(root, output, {}, "nuremberg-")
            graph = Graph().parse(output / "nuremberg-tile-1-map.ttl")
            self.assertEqual(list(graph.objects(None, URIRef("https://w3id.org/cartograph#imageUrl"))),
                             [URIRef((output / "nuremberg-tile-1-map.png").as_uri())])


if __name__ == "__main__":
    unittest.main()
