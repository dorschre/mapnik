"""Catalog regressions for presentation coverage and terrain context."""
import unittest
from map_symbology.pipeline.service import load_symbology_catalog
from map_symbology.resource_paths import default_catalog_path
from map_symbology.rendering.class_builder import build_render_classes
from map_symbology.evaluation.filter_evaluator import PartialFilterEvaluator
from map_symbology.evaluation.functions import RuleFeature, Sk50FunctionEvaluator


class SettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog=load_symbology_catalog(default_catalog_path())

    def test_default_keeps_stadium_fields_shields_and_railway_details(self):
        from render_dtk50 import PRESENTATION_MODE
        classes=build_render_classes(self.catalog, presentation_mode=PRESENTATION_MODE)
        emitted={(rule,c.geometry_type) for c in classes for rule in c.rule_ids}
        for rule in ('RUL04557','RUL04550','RUL00950','RUL02070'):
            self.assertIn((rule,'point'),emitted)
        self.assertIn(('RUL02070','curve'),emitted)

    def test_standard_railway_keeps_line_without_presentation_only_marker(self):
        classes=build_render_classes(self.catalog,feature_types=['AX_Bahnstrecke'],presentation_mode='stdpraes')
        kinds={c.geometry_type for c in classes if 'RUL02070' in c.rule_ids}
        self.assertIn('curve',kinds)
        self.assertNotIn('point',kinds)

    def test_standard_hospital_uses_one_symbol_rule(self):
        classes=build_render_classes(self.catalog,feature_types=['AX_Gebaeude'],presentation_mode='stdpraes')
        point_rules={rule for c in classes if c.geometry_type=='point' for rule in c.rule_ids}
        self.assertNotIn('RUL00055',point_rules)
        self.assertIn('RUL00110',point_rules)

    def test_hill_contour_requires_terrain_context(self):
        from shapely.geometry import LineString
        feature=RuleFeature(layer='L__AX_Hoehenlinie',objid='contour',properties={'hoeheVonHoehenlinie':170},geometry=LineString([(0,0),(100,0)]))
        rule=next(r for r in self.catalog.rules if r.id=='RUL07200')
        configured=PartialFilterEvaluator(Sk50FunctionEvaluator(terrain_zone='huegelland'))
        missing=PartialFilterEvaluator(Sk50FunctionEvaluator())
        self.assertTrue(configured.evaluate_rule(rule,feature).matched)
        self.assertFalse(missing.evaluate_rule(rule,feature).matched)


if __name__=='__main__':
    unittest.main()
