"""Regression checks for pixel-edge alignment and reference tile planning."""
import math
import unittest

from fetch_references import tile_plan
from prepare_areas import DEFAULT_AREAS, align


class AlignmentTests(unittest.TestCase):
    def test_all_views_have_exact_shared_pixel_grid(self):
        for center in DEFAULT_AREAS:
            area = align(center)
            left, top, right, bottom = area['pixel_bounds']
            self.assertEqual((right - left, bottom - top), (640, 640))
            west, south, east, north = area['bbox']
            extent = area['extent_3857']
            projected = [math.radians(west) * 6378137,
                         math.asinh(math.tan(math.radians(south))) * 6378137,
                         math.radians(east) * 6378137,
                         math.asinh(math.tan(math.radians(north))) * 6378137]
            for actual, expected in zip(projected, extent):
                self.assertAlmostEqual(actual, expected, places=6)
            xs, ys = tile_plan(area['pixel_bounds'])
            self.assertLessEqual(len(xs) * len(ys), 16)

    def test_exclusive_bottom_and_right_edges_do_not_fetch_extra_tiles(self):
        xs, ys = tile_plan([256, 512, 768, 1024])
        self.assertEqual(list(xs), [1, 2])
        self.assertEqual(list(ys), [2, 3])

    def test_fractional_crop_is_rejected(self):
        with self.assertRaises(ValueError):
            tile_plan([256.5, 512, 768, 1024])


if __name__ == '__main__':
    unittest.main()
