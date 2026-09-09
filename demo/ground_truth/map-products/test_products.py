"""Regression tests for provenance and SQL identity through real query shapes."""
import json
from pathlib import Path
import tempfile
import unittest

import prepare
from render import enrich_and_check


class IdentityTests(unittest.TestCase):
    def test_empty_boundary_geometries_are_not_exported(self):
        for geometry in (None, {'type': 'LineString', 'coordinates': []},
                         {'type': 'Polygon', 'coordinates': [[]]},
                         {'type': 'GeometryCollection', 'geometries': []}):
            self.assertFalse(prepare.has_coordinates(geometry))
        self.assertTrue(prepare.has_coordinates({'type': 'Point', 'coordinates': [0, 0]}))
        self.assertTrue(prepare.has_coordinates({'type': 'LineString', 'coordinates': [[0, 0], [1, 1]]}))

    def test_nested_union_and_scalar_query(self):
        query = """(select way, name from
          (select ST_PointOnSurface(way) as way, name from planet_osm_polygon
           union all select way, name from planet_osm_point) points
          where name in (select name from planet_osm_line)) as labels"""
        patched = prepare.with_identity(query)
        self.assertEqual(patched.count('osm_type'), 3)
        self.assertIn('(SELECT name FROM planet_osm_line)', patched)

    def test_preserves_quoted_literals(self):
        query = "(select way, 'select from union all' as name from planet_osm_point) as p"
        self.assertIn("'select from union all'", prepare.with_identity(query))

    def test_existing_ids_are_not_duplicated(self):
        query = '(select way, osm_id, osm_type from planet_osm_point) as p'
        patched = prepare.with_identity(query)
        self.assertEqual(patched.count('osm_id'), 1)
        self.assertEqual(patched.count('osm_type'), 1)

    def test_aggregate_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'multi-feature'):
            prepare.with_identity('(select ST_Union(way) as way from planet_osm_polygon) as p')

    def test_missing_osm_identity_is_not_silently_accepted(self):
        data = {'objects': [{'identity': {'layer': 'roads', 'source_id': 'osm:way/1'}}]}
        with self.assertRaisesRegex(ValueError, 'invalid OSM'):
            enrich_and_check(data, {'layers': []})

    def test_raster_identity_does_not_invent_osm_id(self):
        with tempfile.TemporaryDirectory() as directory:
            raster = Path(directory)/'hillshade.tif'; raster.write_bytes(b'test raster content')
            data = {'objects': [
                {'identity': {'layer': 'hillshade', 'feature_id': 1}},
                {'identity': {'layer': 'roads', 'source_id': 'osm:relation/12', 'osm_id': -12, 'osm_type': 'relation'}}]}
            result = enrich_and_check(data, {'layers': [dict(layer='hillshade', source='elevation raster', file=str(raster))]})
            self.assertEqual(result, dict(derived_objects=1, visible_objects=2, osm_objects=1))
            self.assertTrue(data['objects'][0]['identity']['source_id'].startswith('dem:raster:'))
            self.assertNotIn('osm_id', data['objects'][0]['identity'])


if __name__ == '__main__':
    unittest.main()
