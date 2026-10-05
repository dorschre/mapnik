"""Lossless cache/snapshot compaction for disk-bounded batches."""
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlencode

from run import CompressedRetryClient, compact_snapshot, restore_snapshot


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_snapshot_round_trip_preserves_nested_files(self):
        snapshot = self.root / 'Saxony'
        (snapshot / 'nested').mkdir(parents=True)
        files = {'manifest.json': b'{"version": 1}', 'nested/source.ttl': b'RDF\n' * 1000}
        for name, data in files.items():
            (snapshot / name).write_bytes(data)
        compact_snapshot(snapshot)
        self.assertFalse(snapshot.exists())
        restore_snapshot(snapshot)
        for name, data in files.items():
            self.assertEqual((snapshot / name).read_bytes(), data)

    def test_corrupt_archive_is_rejected_before_restore(self):
        snapshot = self.root / 'Hesse'
        snapshot.mkdir()
        (snapshot / 'source.ttl').write_bytes(b'RDF')
        compact_snapshot(snapshot)
        snapshot.with_suffix('.tar.gz').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            restore_snapshot(snapshot)
        self.assertFalse(snapshot.exists())

    def test_external_snapshot_symlink_is_preserved(self):
        original = self.root / 'original'
        original.mkdir()
        (original / 'source.ttl').write_bytes(b'RDF')
        link = self.root / 'NRW'
        link.symlink_to(original, target_is_directory=True)
        compact_snapshot(link)
        self.assertTrue(link.is_symlink())
        self.assertEqual((original / 'source.ttl').read_bytes(), b'RDF')

    def test_get_and_post_cache_round_trips_without_network(self):
        url = 'https://invalid.local/test'
        form = {'query': 'SELECT * WHERE { ?s ?p ?o }'}
        data = b'RDF\n' * 1000
        for method in ('get', 'post'):
            key = hashlib.sha256(url.encode() if method == 'get' else
                                 b'POST\n' + url.encode() + b'\n' + urlencode(form).encode()).hexdigest()
            body = self.root / (key + '.body')
            packed = self.root / (key + '.body.gz')
            body.write_bytes(data)
            (self.root / (key + '.json')).write_text(json.dumps({'sha256': hashlib.sha256(data).hexdigest()}))
            client = CompressedRetryClient(self.root)
            for _ in range(2):
                result = client.get(url) if method == 'get' else client.post(url, form)
                self.assertEqual(result[0], data)
                self.assertFalse(body.exists())
                self.assertEqual(gzip.decompress(packed.read_bytes()), data)


if __name__ == '__main__':
    unittest.main()
