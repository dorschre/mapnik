"""In-process visual ground truth from Mapnik, via ctypes.

Renders a Mapnik stylesheet and returns the image together with the machine-
readable description of what is *actually observable* in that image - collected
during the same AGG pass, not by a second render.

    import mapnik_ground_truth as gt

    gt.setup(plugin_dir="build/out/plugins/input",
             font_dir="fonts/dejavu-fonts-ttf-2.37/ttf")

    result = gt.render("demo/ground_truth/osm/osm.xml",
                       width=1200, height=900,
                       extent_wgs84=(13.3750, 52.5135, 13.3950, 52.5225),
                       identity=["osm_id"])

    open("map.png", "wb").write(result.png)
    for obj in result.objects:
        if obj.label:
            print(obj.layer, obj.feature_id, obj.label, obj.visible_pixel_count)

Only `render` touches the C library; everything it returns is plain Python and
stays valid after the underlying result is freed.

Requires no third-party package. `result.image` needs Pillow and
`result.array` needs numpy, but only if you ask for them.
"""

import ctypes
import ctypes.util
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = ["setup", "render", "Result", "VisibleObject", "DisplayElement", "GroundTruthError", "load_library"]

_LIB_NAME = "libmapnik-ground-truth-c.so"


class GroundTruthError(RuntimeError):
    """Raised when the renderer reports a failure."""


def _candidate_paths():
    override = os.environ.get("MAPNIK_GROUND_TRUTH_LIB")
    if override:
        yield override
    here = os.path.dirname(os.path.abspath(__file__))
    # alongside this file, and in the usual build output directory
    yield os.path.join(here, _LIB_NAME)
    yield os.path.join(here, "..", "..", "build", "out", _LIB_NAME)
    yield os.path.join(os.getcwd(), "build", "out", _LIB_NAME)
    yield _LIB_NAME


_lib = None


def load_library(path: Optional[str] = None):
    """Loads and caches the shared library. Called automatically on first use."""
    global _lib
    if _lib is not None and path is None:
        return _lib

    tried = []
    for candidate in ([path] if path else _candidate_paths()):
        candidate = os.path.normpath(candidate)
        try:
            lib = ctypes.CDLL(candidate)
            break
        except OSError as error:
            tried.append("{}: {}".format(candidate, error))
    else:
        raise GroundTruthError(
            "could not load {}. Set MAPNIK_GROUND_TRUTH_LIB to its path.\n  {}".format(
                _LIB_NAME, "\n  ".join(tried)
            )
        )

    lib.mapnik_gt_last_error.restype = ctypes.c_char_p
    lib.mapnik_gt_setup.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    lib.mapnik_gt_setup.restype = ctypes.c_int
    lib.mapnik_gt_render.argtypes = [
        ctypes.c_char_p,                    # xml
        ctypes.c_int,                       # xml_is_string
        ctypes.c_uint, ctypes.c_uint,       # width, height
        ctypes.POINTER(ctypes.c_double),    # extent
        ctypes.c_int,                       # extent_is_wgs84
        ctypes.c_double,                    # scale_factor
        ctypes.c_double,                    # simplify_tolerance
        ctypes.c_int,                       # collect_attributes
        ctypes.POINTER(ctypes.c_char_p),    # identity names
        ctypes.c_int,                       # identity count
        ctypes.c_char_p,                    # base_path
    ]
    lib.mapnik_gt_render.restype = ctypes.c_void_p
    lib.mapnik_gt_json.argtypes = [ctypes.c_void_p]
    lib.mapnik_gt_json.restype = ctypes.c_char_p
    lib.mapnik_gt_png.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
    lib.mapnik_gt_png.restype = ctypes.POINTER(ctypes.c_ubyte)
    lib.mapnik_gt_rgba.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(ctypes.c_uint),
    ]
    lib.mapnik_gt_rgba.restype = ctypes.POINTER(ctypes.c_ubyte)
    for name in ("mapnik_gt_displayed_count", "mapnik_gt_rendered_count", "mapnik_gt_object_count"):
        function = getattr(lib, name)
        function.argtypes = [ctypes.c_void_p]
        function.restype = ctypes.c_size_t
    lib.mapnik_gt_free.argtypes = [ctypes.c_void_p]
    lib.mapnik_gt_free.restype = None

    _lib = lib
    return _lib


def _error(lib) -> str:
    message = lib.mapnik_gt_last_error()
    return message.decode("utf-8", "replace") if message else "unknown error"


def setup(plugin_dir: Optional[str] = None, font_dir: Optional[str] = None, library: Optional[str] = None):
    """Registers datasource plugins and fonts. Call once, before render()."""
    lib = load_library(library)
    encode = lambda text: text.encode("utf-8") if text else None
    if lib.mapnik_gt_setup(encode(plugin_dir), encode(font_dir)) != 0:
        raise GroundTruthError(_error(lib))


@dataclass
class VisibleObject:
    """A source feature with at least one graphical manifestation in the image."""

    layer: str
    feature_id: int
    #: Links the object back to source data. Never implies visibility.
    identity: Dict[str, Any] = field(default_factory=dict)
    #: Values actually manifested in the image, e.g. {"label": "Unter den Linden"}.
    visible_properties: Dict[str, Any] = field(default_factory=dict)
    #: Datasource attributes that are *not* visually expressed.
    source_properties: Dict[str, Any] = field(default_factory=dict)
    displayed_element_ids: List[int] = field(default_factory=list)
    visible_pixel_count: int = 0
    visible_pixel_bbox: Optional[Tuple[float, float, float, float]] = None
    #: GeoJSON MultiPolygon in output pixel coordinates, or None.
    visible_geometry: Optional[Dict[str, Any]] = None

    @property
    def label(self) -> Optional[str]:
        """The text this object visibly carries, if any."""
        return self.visible_properties.get("label")

    def rings(self):
        """Yields every boundary ring as a list of (x, y) pixel pairs.

        The first ring of each part is its exterior, the rest are holes.
        """
        if not self.visible_geometry:
            return
        for polygon in self.visible_geometry["coordinates"]:
            for ring in polygon:
                yield [(point[0], point[1]) for point in ring]


@dataclass
class DisplayElement:
    """One concrete graphical occurrence that survived to the final image."""

    id: int
    sequence: int
    type: str
    layer: str
    feature_id: int
    #: 0 point, 1 line, 2 area
    anchor_dimension: int = 2
    placement: str = ""
    #: True when the visible pixels touch the canvas border.
    clipped: bool = False
    pixel_count: int = 0
    pixel_bbox: Optional[Tuple[float, float, float, float]] = None
    rendered_text: Optional[str] = None
    #: Styling in effect for this occurrence, e.g. {"kind": "stroke", ...}.
    portrayal: Dict[str, Any] = field(default_factory=dict)
    #: GeoJSON MultiPolygon in output pixel coordinates, or None.
    pixel_geometry: Optional[Dict[str, Any]] = None


@dataclass
class Result:
    png: bytes
    rgba: bytes
    width: int
    height: int
    ground_truth: Dict[str, Any]
    #: Canvas description: width, height, crs, extent, background_color, ...
    map: Dict[str, Any]
    elements: List[DisplayElement]
    objects: List[VisibleObject]
    displayed_element_count: int
    rendered_element_count: int

    @property
    def invisible_element_count(self) -> int:
        """Occurrences that were drawn but left no visible pixel."""
        return self.rendered_element_count - self.displayed_element_count

    @property
    def image(self):
        """The map as a Pillow image (requires Pillow)."""
        from PIL import Image

        return Image.frombytes("RGBA", (self.width, self.height), self.rgba)

    @property
    def array(self):
        """The map as a (height, width, 4) uint8 numpy array (requires numpy)."""
        import numpy

        return numpy.frombuffer(self.rgba, dtype=numpy.uint8).reshape(self.height, self.width, 4)

    def elements_of(self, obj: VisibleObject) -> List[DisplayElement]:
        """The individual graphical occurrences that make up a visible object."""
        wanted = set(obj.displayed_element_ids)
        return [element for element in self.elements if element.id in wanted]

    def identity_coverage(self, name: str = "osm_id"):
        """(objects carrying this identity attribute, total, {layer: missing}).

        A visible object without identity metadata cannot be linked back to
        source data. Stylesheet queries often select no identifier at all, so
        this is worth checking rather than assuming.
        """
        missing: Dict[str, int] = {}
        found = 0
        for obj in self.objects:
            if name in obj.identity:
                found += 1
            else:
                missing[obj.layer] = missing.get(obj.layer, 0) + 1
        return found, len(self.objects), missing

    def by_layer(self, name: str) -> List[VisibleObject]:
        return [obj for obj in self.objects if obj.layer == name]

    def labelled(self) -> List[VisibleObject]:
        return [obj for obj in self.objects if obj.label is not None]

    def save(self, path: str):
        with open(path, "wb") as handle:
            handle.write(self.png)


def _to_element(raw: Dict[str, Any]) -> DisplayElement:
    box = raw.get("pixel_bbox")
    return DisplayElement(
        id=raw["id"],
        sequence=raw["sequence"],
        type=raw["type"],
        layer=raw.get("layer", ""),
        feature_id=raw.get("feature_id", -1),
        anchor_dimension=raw.get("anchor_dimension", 2),
        placement=raw.get("placement", ""),
        clipped=bool(raw.get("clipped", False)),
        pixel_count=raw.get("pixel_count", 0),
        pixel_bbox=tuple(box) if box else None,
        rendered_text=raw.get("rendered_text"),
        portrayal=dict(raw.get("portrayal", {})),
        pixel_geometry=raw.get("pixel_geometry"),
    )


def _to_object(raw: Dict[str, Any]) -> VisibleObject:
    identity = dict(raw.get("identity", {}))
    box = raw.get("visible_pixel_bbox")
    return VisibleObject(
        layer=identity.get("layer", ""),
        feature_id=identity.get("feature_id", -1),
        identity=identity,
        visible_properties=dict(raw.get("visible_properties", {})),
        source_properties=dict(raw.get("source_properties", {})),
        displayed_element_ids=list(raw.get("displayed_elements", [])),
        visible_pixel_count=raw.get("visible_pixel_count", 0),
        visible_pixel_bbox=tuple(box) if box else None,
        visible_geometry=raw.get("visible_geometry"),
    )


def render(
    stylesheet: str,
    width: int = 1024,
    height: int = 768,
    extent: Optional[Sequence[float]] = None,
    extent_wgs84: Optional[Sequence[float]] = None,
    scale_factor: float = 1.0,
    simplify_tolerance: float = 0.5,
    collect_attributes: bool = False,
    identity: Sequence[str] = (),
    is_string: bool = False,
    base_path: str = "",
    library: Optional[str] = None,
) -> Result:
    """Renders a stylesheet and returns the image plus its visual ground truth.

    stylesheet          path to Mapnik XML, or the XML itself with is_string=True
    extent              minx, miny, maxx, maxy in the map's own srs
    extent_wgs84        the same as lon/lat; reprojected for you
                        (omit both to zoom to the full data extent)
    simplify_tolerance  pixels; 0 keeps the exact pixel-corner outlines
    collect_attributes  keep datasource attributes as source_properties. They
                        are never treated as visible information either way.
    identity            attribute names to report as identity metadata
    base_path           resolves relative datasource paths for is_string=True
    """
    lib = load_library(library)

    if extent is not None and extent_wgs84 is not None:
        raise ValueError("pass either extent or extent_wgs84, not both")
    box = extent if extent is not None else extent_wgs84
    if box is not None:
        if len(box) != 4:
            raise ValueError("extent must be (minx, miny, maxx, maxy)")
        extent_array = (ctypes.c_double * 4)(*[float(v) for v in box])
    else:
        extent_array = None

    names = [str(name).encode("utf-8") for name in identity]
    identity_array = (ctypes.c_char_p * len(names))(*names) if names else None

    handle = lib.mapnik_gt_render(
        stylesheet.encode("utf-8"),
        1 if is_string else 0,
        ctypes.c_uint(width),
        ctypes.c_uint(height),
        extent_array,
        1 if extent_wgs84 is not None else 0,
        ctypes.c_double(scale_factor),
        ctypes.c_double(simplify_tolerance),
        1 if collect_attributes else 0,
        identity_array,
        len(names),
        base_path.encode("utf-8") if base_path else None,
    )
    if not handle:
        raise GroundTruthError(_error(lib))

    try:
        png_size = ctypes.c_size_t()
        png_ptr = lib.mapnik_gt_png(handle, ctypes.byref(png_size))
        png = ctypes.string_at(png_ptr, png_size.value)

        rgba_size = ctypes.c_size_t()
        out_width = ctypes.c_uint()
        out_height = ctypes.c_uint()
        rgba_ptr = lib.mapnik_gt_rgba(
            handle, ctypes.byref(rgba_size), ctypes.byref(out_width), ctypes.byref(out_height)
        )
        rgba = ctypes.string_at(rgba_ptr, rgba_size.value)

        truth = json.loads(lib.mapnik_gt_json(handle).decode("utf-8"))
        return Result(
            png=png,
            rgba=rgba,
            width=out_width.value,
            height=out_height.value,
            ground_truth=truth,
            map=dict(truth.get("map", {})),
            elements=[_to_element(raw) for raw in truth.get("elements", [])],
            objects=[_to_object(raw) for raw in truth.get("objects", [])],
            displayed_element_count=lib.mapnik_gt_displayed_count(handle),
            rendered_element_count=lib.mapnik_gt_rendered_count(handle),
        )
    finally:
        lib.mapnik_gt_free(handle)
