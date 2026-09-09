"""Regression checks for the ADV raster coordinate contract and invalid images."""
from io import BytesIO
import unittest
from urllib.parse import parse_qs, urlparse

from PIL import Image
from fetch_dtk50_references import check_image, request_url
from prepare_areas import align


class ReferenceTests(unittest.TestCase):
    def test_explicit_dtk50_layer_and_projected_bounds(self):
        for state,layer in [('NRW','nw_dtk50_col'),('Saxony','sn_dtk50_p_color')]:
            area=align(dict(lon=6.96,lat=50.94,states=[state]),zoom=14,size=640)
            query=parse_qs(urlparse(request_url(area)).query)
            self.assertEqual(query['layer'],[layer])
            self.assertEqual(query['crs'],['EPSG:3857'])
            self.assertEqual(list(map(float,query['bbox'][0].split(','))),area['extent_3857'])
            self.assertEqual(query['width'],['640'])
            self.assertNotIn('product',query)

    def test_blank_success_response_is_rejected(self):
        stream=BytesIO()
        Image.new('RGB',(640,640),'white').save(stream,format='PNG')
        with self.assertRaisesRegex(ValueError,'Blank reference'):
            check_image(stream.getvalue(),640)

    def test_wrong_image_dimensions_are_rejected(self):
        stream=BytesIO()
        Image.new('RGB',(256,256),'white').save(stream,format='PNG')
        with self.assertRaisesRegex(ValueError,'exact requested pixel grid'):
            check_image(stream.getvalue(),640)


if __name__=='__main__':
    unittest.main()
