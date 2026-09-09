"""Mapnik-only corrections to the companion's generated DTK50 stylesheet.

The companion's matching, feature selection, geometry, placement and resources
are left untouched. Its raster integer conversions are not appropriate for
Mapnik's subpixel symbolizers. SVG marker dimensions also mean path bounds in
Mapnik, not the painted (stroke-inclusive) bounds used by the raster backend.
"""
from dataclasses import replace
from functools import lru_cache
import math
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from map_symbology.mapnik.style import build_stylesheet as companion_stylesheet
from map_symbology.rendering.ir_bridge import _style_for_render_class


def symbol_scales(catalog):
    """Read graphic scale factors without modifying catalog or matching logic.

    The supplied SVG resources preserve raw path coordinates but omit these
    graphic transforms. A uniform factor belongs in the Mapnik symbolizer.
    Mixed per-member scales cannot be represented by a single transform and
    are flagged rather than guessed.
    """
    from map_symbology.catalog.mdl_parser import (
        MdlLine, _find_property, _property_text, _split_object_blocks,
    )

    def collect(lines, parent=1.0):
        values = []
        kinds = {'CompositeGraphic', 'AreaGraphic', 'LineGraphic', 'TextGraphic'}
        for block in _split_object_blocks(lines, kinds):
            scale = parent * float(_property_text(block.lines, 'scalefactor') or 1)
            if not math.isfinite(scale) or scale <= 0:
                raise ValueError('Invalid catalog graphic scale')
            if block.kind == 'CompositeGraphic':
                members = _find_property(block.lines, 'members')
                if members:
                    values.extend(collect(members.lines, scale))
            elif block.kind != 'TextGraphic':
                values.append(scale)
        return values

    result = {}
    for rule in catalog.rules:
        for emit in rule.emits:
            symbol = emit.symbolizer
            if not symbol or symbol.kind != 'PointSymbolizer' or symbol.id in result:
                continue
            lines = [MdlLine(i + 1, line) for i, line in enumerate(symbol.raw.splitlines())]
            graphic = _find_property(lines, 'graphic')
            values = collect(graphic.lines) if graphic else []
            if values:
                result[symbol.id] = values[0] if all(math.isclose(v, values[0]) for v in values) else None
    return result


@lru_cache(maxsize=1)
def default_symbol_scales():
    from map_symbology.catalog.extract import load_symbology_catalog
    from map_symbology.resource_paths import default_catalog_path
    return symbol_scales(load_symbology_catalog(default_catalog_path()))


def scale_for_mapnik_viewport(area, render_scale, map_scale_denominator=50000.0):
    """Use the rendered viewport's ground resolution, not its UTM envelope.

    Reprojecting a north-up Mercator viewport to UTM gives a rotated footprint.
    The bounding envelope is wider than the footprint, so dividing its width
    by the image width underestimates every physical symbol size. Measure one
    horizontal output pixel at the viewport centre in the datasource's metric
    CRS instead. Positions and source-sized pixel dimensions are not changed.
    """
    from pyproj import Transformer
    xmin, ymin, xmax, ymax = map(float, area['extent_3857'])
    size = int(area['size'])
    if xmax <= xmin or ymax <= ymin or size <= 0 or map_scale_denominator <= 0:
        raise ValueError('Invalid Mapnik viewport or map scale')
    x, y = (xmin + xmax) / 2, (ymin + ymax) / 2
    half_pixel = (xmax - xmin) / size / 2
    transform = Transformer.from_crs(3857, 25832, always_xy=True)
    m_per_px = math.dist(transform.transform(x - half_pixel, y), transform.transform(x + half_pixel, y))
    if not math.isfinite(m_per_px) or m_per_px <= 0:
        raise ValueError('Cannot resolve metric Mapnik viewport scale')
    dpi = 25.4 * map_scale_denominator / (1000 * m_per_px)
    return replace(render_scale, m_per_px=m_per_px, dpi=dpi)


def number(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Non-finite Mapnik style dimension")
    return format(value, '.12g')


def line_symbolizers(stroke, unit_px):
    """Translate a compiled stroke without integer rounding or a 1 px floor."""
    sections = stroke.get('compound_pattern')
    if sections:
        period = sum(float(s.get('length', 0) or 0) for s in sections) * unit_px
        if period <= 0:
            raise ValueError('Compound stroke has no positive period')
        offset = 0.0
        result = []
        for section in sections:
            length = float(section.get('length', 0) or 0) * unit_px
            if section.get('kind') == 'solid':
                width = float(section.get('width', 15)) * unit_px
                color = section.get('color', '#1d1d1b')
                if length > 0:
                    attrs = {'stroke': color, 'stroke-width': number(width)}
                    if length < period:
                        attrs['stroke-dasharray'] = f'{number(length)}, {number(period-length)}'
                        attrs['stroke-dashoffset'] = number(-offset)
                    if section.get('linecaps'):
                        attrs['stroke-linecap'] = section['linecaps']
                    result.append(ET.Element('LineSymbolizer', attrs))
                elif section.get('linecaps') == 'round':
                    result.append(ET.Element('MarkersSymbolizer', {
                        'marker-type': 'ellipse', 'fill': color,
                        'width': number(width), 'height': number(width),
                        'placement': 'line', 'spacing': number(period),
                        'spacing-offset': number(offset), 'max-error': '0',
                        'allow-overlap': 'true', 'ignore-placement': 'true',
                    }))
            offset += length
        return result
    attrs = {'stroke': stroke.get('color', '#1d1d1b'),
             'stroke-width': number(float(stroke.get('width', 15)) * unit_px)}
    if stroke.get('dasharray'):
        values = list(stroke['dasharray'])
        if len(values) % 2:
            values *= 2
        attrs['stroke-dasharray'] = ', '.join(number(float(v) * unit_px) for v in values)
    for key in ('linecap', 'linejoin'):
        if stroke.get(key):
            attrs['stroke-' + key] = stroke[key]
    return [ET.Element('LineSymbolizer', attrs)]


def svg_unit_transform(path, unit_px):
    """Map SVG user units to pixels without fitting its stroked bounds twice.

    The exported ATKIS SVGs use catalog coordinates in their viewBox, with an
    arbitrary preview width/height (usually 36 px). Mapnik applies that viewport
    transform first. Undo its scale, then apply the physical catalog scale.
    """
    root = ET.parse(path).getroot()
    box = [float(v) for v in root.get('viewBox', '').replace(',', ' ').split()]
    if len(box) != 4 or min(box[2:]) <= 0:
        raise ValueError(f'SVG has no valid catalog viewBox: {path}')
    width = float(root.get('width', box[2]))
    height = float(root.get('height', box[3]))
    if min(width, height) <= 0:
        raise ValueError(f'SVG has no positive viewport: {path}')
    sx, sy = width / box[2], height / box[3]
    aspect = root.get('preserveAspectRatio', 'xMidYMid meet')
    if 'none' not in aspect:
        sx = sy = max(sx, sy) if 'slice' in aspect else min(sx, sy)
    return f'scale({number(unit_px/sx)}, {number(unit_px/sy)})'


def sized_svg_transform(path, spec):
    """Fit the supplied painted dimensions, not Mapnik's unstroked path bounds.

    Use the same catalog extent that defines the pipeline's supplied size.
    Applying width/height directly fits the path and then enlarges its stroke
    again, especially for the stadium's broad grey stand.
    """
    from map_symbology.raster.pillow_draw import _parsed_path_graphics, _path_graphics_bounds
    graphics = _parsed_path_graphics(spec)
    if not graphics:
        return None
    left, bottom, right, top = _path_graphics_bounds(graphics)
    # svg_unit_transform(1) undoes SVG viewport scaling to catalog coordinates.
    match = re.fullmatch(r'scale\(([^,]+), ([^)]+)\)', svg_unit_transform(path, 1))
    sx, sy = map(float, match.groups())
    if right <= left or top <= bottom:
        raise ValueError('Source-sized symbol has no positive painted extent')
    return (f'scale([size_w_px] * {number(sx/(right-left))}, '
            f'[size_h_px] * {number(sy/(top-bottom))}) rotate([rotation])')


def correct_tree(tree, styles, render_scale, graphic_scales=None, original_scale=None):
    """Change symbolizer attributes only; preserve every layer and rule filter."""
    if render_scale.mode != 'dtk50':
        raise ValueError('DTK50 stylesheet corrections require physical render units')
    unit_px = render_scale.catalog_unit_mm * render_scale.px_per_mm
    changes = {'fractional_line_styles': 0, 'native_svg_sizes': 0,
               'clockwise_marker_rotations': 0, 'fractional_text_sizes': 0,
               'catalog_graphic_scales': 0, 'viewport_pattern_scales': 0,
               'painted_source_sizes': 0}
    for style in tree.findall('Style'):
        match = re.fullmatch(r's\d+_(surface|curve|point|label)_(RUL\d+)(?:_(\d+))?', style.get('name', ''))
        if not match:
            continue
        kind, rule_id, stroke_index = match.groups()
        spec = styles[(kind, rule_id)]
        for rule in style.findall('Rule'):
            if kind == 'curve':
                strokes = spec.get('strokes', ())
                if stroke_index is not None:
                    strokes = strokes[int(stroke_index):int(stroke_index)+1]
                for child in list(rule):
                    if child.tag.endswith('Symbolizer'):
                        rule.remove(child)
                for stroke in strokes:
                    rule.extend(line_symbolizers(stroke, unit_px))
                changes['fractional_line_styles'] += 1
            elif kind == 'surface':
                # Outline strokes precede pattern fills in the original XML.
                indices = [i for i, e in enumerate(rule) if e.tag == 'LineSymbolizer']
                insert_at = indices[0] if indices else next(
                    (i for i, e in enumerate(rule) if e.tag == 'PolygonPatternSymbolizer'), len(rule))
                for i in reversed(indices):
                    rule.remove(rule[i])
                # A configured outline_width is already pixels. The companion
                # casts it to int, which discards e.g. a valid 0.5 px outline.
                outline_width = float(spec.get('outline_width', 0) or 0)
                if outline_width > 0:
                    rule.insert(insert_at, ET.Element('LineSymbolizer', {
                        'stroke': spec.get('outline') or spec.get('fill') or '#1d1d1b',
                        'stroke-width': number(outline_width),
                    }))
                    insert_at += 1
                for stroke in spec.get('outline_strokes', ()):
                    for element in line_symbolizers(stroke, unit_px):
                        rule.insert(insert_at, element)
                        insert_at += 1
                changes['fractional_line_styles'] += 1
                if original_scale is not None:
                    ratio = render_scale.px_per_mm / original_scale.px_per_mm
                    for pattern in rule.findall('PolygonPatternSymbolizer'):
                        transform = pattern.get('transform', '')
                        pattern.set('transform', f'{transform} scale({number(ratio)})'.strip())
                        changes['viewport_pattern_scales'] += 1
            elif kind == 'point':
                for marker in rule.findall('MarkersSymbolizer'):
                    # AGG's positive rotation is clockwise in screen space,
                    # matching the supplied rotation property. TextSymbolizer's
                    # orientation convention differs and must stay negative.
                    if marker.get('transform') == 'rotate(-[rotation])':
                        marker.set('transform', 'rotate([rotation])')
                        changes['clockwise_marker_rotations'] += 1
                    embedded = spec.get('embedded_text') or {}
                    asset = marker.get('file', '')
                    sized = (marker.get('width', '').startswith('[')
                             or '[size_w_px]' in marker.get('transform', ''))
                    if asset and sized and not embedded.get('label_text'):
                        transform = sized_svg_transform(Path(asset), spec)
                        if transform:
                            marker.attrib.pop('width', None)
                            marker.attrib.pop('height', None)
                            marker.set('transform', transform)
                            changes['painted_source_sizes'] += 1
                    if asset and not sized and not embedded.get('label_text'):
                        symbol_id = Path(asset).name.split('.')[0]
                        graphic_scale = (graphic_scales or {}).get(symbol_id, 1.0)
                        if graphic_scale is None:
                            raise ValueError(f'{symbol_id}: mixed graphic scales require a corrected SVG asset')
                        transform = svg_unit_transform(Path(asset), unit_px * graphic_scale)
                        marker.attrib.pop('width', None)
                        marker.attrib.pop('height', None)
                        marker.set('transform', f'{transform} rotate([rotation])')
                        changes['native_svg_sizes'] += 1
                        changes['catalog_graphic_scales'] += graphic_scale != 1
            for text in rule.findall('TextSymbolizer'):
                text_spec = spec.get('embedded_text', {}) if kind == 'point' else spec
                font_pt = float((text_spec.get('font') or {}).get('font_size', 0) or 0)
                if font_pt <= 0:
                    font_pt = render_scale.default_label_font_pt
                text.set('size', number(font_pt * render_scale.dpi / 72))
                changes['fractional_text_sizes'] += 1
    return changes


def build_stylesheet(datasource, class_by_bucket, **kwargs):
    sheet = companion_stylesheet(datasource, class_by_bucket, **kwargs)
    tree = ET.fromstring(sheet.xml)
    styles = {key: _style_for_render_class(value, key[1]) for key, value in class_by_bucket.items()}
    correct_tree(tree, styles, kwargs['render_scale'], default_symbol_scales())
    return replace(sheet, xml=ET.tostring(tree, encoding='unicode'))
