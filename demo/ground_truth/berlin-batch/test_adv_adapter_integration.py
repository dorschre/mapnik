"""Old Mapnik entry points must delegate to the separately owned adapter."""
import unittest
from _adv_adapter import load


class AdapterIntegrationTests(unittest.TestCase):
    def test_legacy_imports_use_component_implementations(self):
        import adv_source
        import fetch_adv
        import enrich_road_parents
        import repair_acquisition
        import compare_states
        self.assertIs(adv_source.AdvFeatureSource, load('source').AdvFeatureSource)
        self.assertIs(fetch_adv.Client, load('fetch_adv').Client)
        self.assertIs(enrich_road_parents.enrich, load('road_parents').enrich)
        self.assertIs(repair_acquisition.repair, load('repair').repair)
        self.assertIs(compare_states.acquire_dlm, load('acquisition').acquire_dlm)
        self.assertIn('adv_dlm50_adapter', adv_source.AdvFeatureSource.__module__)


if __name__ == '__main__':
    unittest.main()
