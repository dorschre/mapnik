"""Exercise stylesheet corrections through the actual Mapnik AGG renderer."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from xml.etree import ElementTree as ET

from PIL import Image
from map_symbology.rendering.scale import RenderScale
from dtk50_stylesheet import correct_tree, line_symbolizers, default_symbol_scales, scale_for_mapnik_viewport

ROOT = Path(__file__).resolve().parents[3]
RENDERER = ROOT / 'build/out/mapnik-ground-truth-render'


class StylesheetTests(unittest.TestCase):
    def test_viewport_resolution_uses_local_metric_scale_not_rotated_envelope(self):
        # UTM zone 32 has the exact 0.9996 scale on its 9-degree central meridian.
        from pyproj import Transformer
        x, y = Transformer.from_crs(4326, 3857, always_xy=True).transform(9, 0)
        area = {'size': 256, 'extent_3857': [x-1280, y-1280, x+1280, y+1280]}
        scale = scale_for_mapnik_viewport(area, RenderScale(mode='dtk50'))
        self.assertAlmostEqual(scale.m_per_px, 9.996, places=5)

    def test_existing_pattern_follows_corrected_viewport_scale(self):
        root = ET.fromstring('''<Map><Style name="s0000_surface_RUL00120"><Rule>
          <PolygonPatternSymbolizer file="existing.svg" alignment="global"/>
          </Rule></Style></Map>''')
        correct_tree(root, {('surface', 'RUL00120'): {}}, RenderScale(mode='dtk50', dpi=105),
                     original_scale=RenderScale(mode='dtk50', dpi=100))
        pattern = root.find('.//PolygonPatternSymbolizer')
        self.assertEqual(pattern.get('transform'), 'scale(1.05)')
        self.assertEqual(pattern.get('file'), 'existing.svg')
        self.assertEqual(pattern.get('alignment'), 'global')

    def test_catalog_tree_row_and_monument_graphic_scales(self):
        scales = default_symbol_scales()
        self.assertEqual(scales['SYM42600'], 0.75)
        self.assertEqual(scales['SYM27300'], 0.85)
        self.assertEqual(scales['SYM24300'], 1.0)

    def test_fractional_width_and_dash_lengths(self):
        line, = line_symbolizers({'width': 4, 'dasharray': [2, 6]}, 0.1)
        self.assertEqual(float(line.get('stroke-width')), 0.4)
        self.assertEqual(list(map(float, line.get('stroke-dasharray').split(','))), [0.2, 0.6])

    def test_compound_gaps_and_zero_length_butt_are_not_dots(self):
        elements = line_symbolizers({'compound_pattern': [
            {'kind': 'solid', 'length': 5, 'width': 4, 'linecaps': 'butt'},
            {'kind': 'gap', 'length': 3},
            {'kind': 'solid', 'length': 0, 'width': 4, 'linecaps': 'butt'},
            {'kind': 'solid', 'length': 0, 'width': 4, 'linecaps': 'round'},
            {'kind': 'gap', 'length': 2},
        ]}, 0.1)
        self.assertEqual([e.tag for e in elements], ['LineSymbolizer', 'MarkersSymbolizer'])
        self.assertAlmostEqual(float(elements[1].get('spacing-offset')), 0.8)
        self.assertEqual(elements[1].get('max-error'), '0')

    def test_source_sized_marker_and_filter_are_preserved(self):
        root = ET.fromstring('''<Map><Style name="s0000_point_RUL00110"><Rule>
          <Filter>[rule_id] = 'RUL00110' and [size_w_px] &gt; 0</Filter>
          <MarkersSymbolizer width="[size_w_px]" height="[size_h_px]" transform="rotate(-[rotation])"/>
          </Rule></Style></Map>''')
        before = root.find('.//Filter').text
        correct_tree(root, {('point', 'RUL00110'): {}}, RenderScale(mode='dtk50'))
        self.assertEqual(root.find('.//Filter').text, before)
        marker = root.find('.//MarkersSymbolizer')
        self.assertEqual(marker.get('width'), '[size_w_px]')
        self.assertEqual(marker.get('height'), '[size_h_px]')
        self.assertEqual(marker.get('transform'), 'rotate([rotation])')

    def test_text_keeps_its_own_rotation_convention(self):
        root = ET.fromstring('''<Map><Style name="s0000_label_RUL08410"><Rule>
          <TextSymbolizer size="6" orientation="-[rotation]">[text]</TextSymbolizer>
          </Rule></Style></Map>''')
        correct_tree(root, {('label', 'RUL08410'): {'font': {'font_size': 5.1}}},
                     RenderScale(mode='dtk50', dpi=50))
        text = root.find('.//TextSymbolizer')
        self.assertAlmostEqual(float(text.get('size')), 5.1 * 50 / 72)
        self.assertEqual(text.get('orientation'), '-[rotation]')

    def test_subpixel_polygon_outline_is_not_discarded(self):
        root = ET.fromstring('''<Map><Style name="s0000_surface_RUL00120"><Rule>
          <PolygonSymbolizer fill="#cccccc"/>
          <PolygonPatternSymbolizer file="existing-pattern.svg"/>
          </Rule></Style></Map>''')
        correct_tree(root, {('surface', 'RUL00120'): {'outline_width': 0.5, 'outline': '#000000'}},
                     RenderScale(mode='dtk50'))
        self.assertEqual(float(root.find('.//LineSymbolizer').get('stroke-width')), 0.5)
        self.assertEqual([e.tag for e in root.find('.//Rule')],
                         ['PolygonSymbolizer', 'LineSymbolizer', 'PolygonPatternSymbolizer'])


@unittest.skipUnless(RENDERER.is_file(), 'Build the Mapnik ground truth renderer')
class MapnikPixelTests(unittest.TestCase):
    def render(self, folder, svg, rotation, corrected, graphic_scale=1.0, sized=False):
        directory = Path(folder)
        asset = directory / 'symbol.svg'
        asset.write_text(svg)
        data = directory / 'points.geojson'
        data.write_text(json.dumps({'type': 'FeatureCollection', 'features': [{
            'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [64, 64]},
            'properties': {'rotation': rotation, 'rule_id': 'RUL00110', 'source_id': 'fixture'},
        }]}))
        tree = ET.fromstring(f'''<Map srs="epsg:3857" background-color="#ffffff">
          <Style name="s0000_point_RUL00110"><Rule>
          <Filter>[rule_id] = 'RUL00110'</Filter>
          <MarkersSymbolizer file="{asset}" width="24" height="24"
              allow-overlap="true" ignore-placement="true" transform="rotate(-[rotation])"/>
          </Rule></Style><Layer name="fixture" srs="epsg:3857">
          <StyleName>s0000_point_RUL00110</StyleName><Datasource>
          <Parameter name="type">geojson</Parameter><Parameter name="file">{data}</Parameter>
          </Datasource></Layer></Map>''')
        spec = {}
        if sized:
            marker = tree.find('.//MarkersSymbolizer')
            marker.set('width', '[size_w_px]')
            marker.set('height', '[size_h_px]')
            payload = json.loads(data.read_text())
            payload['features'][0]['properties'].update(size_w_px=24, size_h_px=24)
            data.write_text(json.dumps(payload))
            spec = {'path_graphics': [{'kind': 'LineGraphic',
                    'path': 'M -50,-50 L 50,-50 L 50,50 L -50,50 Z', 'widths': [20]}]}
        if corrected:
            correct_tree(tree, {('point', 'RUL00110'): spec}, RenderScale(mode='dtk50', dpi=508),
                         {'symbol': graphic_scale})
        xml = directory / 'style.xml'
        ET.ElementTree(tree).write(xml)
        subprocess.run([str(RENDERER), str(xml), str(directory / 'map'),
                        '--size', '128', '128', '--extent', '0', '0', '128', '128',
                        '--plugins', str(ROOT / 'build/out/plugins/input')],
                       check=True, capture_output=True)
        with Image.open(directory / 'map.png') as image:
            pixels = list(image.convert('RGB').get_flattened_data())
        truth = json.loads((directory / 'map.json').read_text())
        return pixels, truth

    def test_mapnik_stroke_is_not_added_to_fitted_marker_width_twice(self):
        svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="36" height="36"
          viewBox="-100 -100 200 200"><path d="M -50,-50 H 50 V 50 H -50 Z"
          fill="black" stroke="black" stroke-width="20"/></svg>'''
        widths = []
        for corrected in (False, True):
            with tempfile.TemporaryDirectory() as folder:
                pixels, truth = self.render(folder, svg, 0, corrected)
                xs = [i % 128 for i, rgb in enumerate(pixels) if max(rgb) < 128]
                widths.append(max(xs) - min(xs) + 1)
                self.assertEqual(len(truth['objects']), 1)
        # 100 catalog units + 20 stroke units, at 0.2 px/unit = 24 px.
        self.assertEqual(widths[1], 24)
        self.assertGreater(widths[0], widths[1] + 3)

    def test_catalog_graphic_scale_applies_to_paths_and_strokes(self):
        svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="36" height="36"
          viewBox="-100 -100 200 200"><path d="M -50,-50 H 50 V 50 H -50 Z"
          fill="black" stroke="black" stroke-width="20"/></svg>'''
        with tempfile.TemporaryDirectory() as folder:
            pixels, _ = self.render(folder, svg, 0, True, graphic_scale=0.75)
            xs = [i % 128 for i, rgb in enumerate(pixels) if max(rgb) < 128]
            self.assertEqual(max(xs) - min(xs) + 1, 18)

    def test_source_sized_marker_fits_painted_extent_in_mapnik(self):
        svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="36" height="36"
          viewBox="-100 -100 200 200"><path d="M -50,-50 H 50 V 50 H -50 Z"
          fill="black" stroke="black" stroke-width="20"/></svg>'''
        widths = []
        for corrected in (False, True):
            with tempfile.TemporaryDirectory() as folder:
                pixels, _ = self.render(folder, svg, 0, corrected, sized=True)
                xs = [i % 128 for i, rgb in enumerate(pixels) if max(rgb) < 128]
                widths.append(max(xs) - min(xs) + 1)
        self.assertEqual(widths[1], 24)
        self.assertGreater(widths[0], widths[1] + 3)

    def test_mapnik_marker_rotation_matches_clockwise_input(self):
        svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="36" height="36"
          viewBox="-60 -60 120 120"><path d="M -50,-10 H 30 V 10 H -50 Z" fill="black"/>
          <path d="M 20,-20 L 50,0 L 20,20 Z" fill="red"/></svg>'''
        offsets = []
        for corrected in (False, True):
            with tempfile.TemporaryDirectory() as folder:
                pixels, _ = self.render(folder, svg, 45, corrected)
                red = [i for i, (r, g, b) in enumerate(pixels) if r > 180 and g < 100 and b < 100]
                offsets.append(sum(i // 128 for i in red) / len(red) - 63.5)
        self.assertLess(offsets[0], -2)
        self.assertGreater(offsets[1], 2)


if __name__ == '__main__':
    unittest.main()
