#!/usr/bin/env python3
"""Turn mapnik visual ground truth into a CartoGraph RDF graph.

    python3 ground_truth_to_rdf.py berlin.json -o berlin.ttl \
        --base https://example.org/berlin/ \
        --source-geojson demo/ground_truth/osm

The input is the JSON written by mapnik-ground-truth-render (or by the Python
binding's `result.ground_truth`). The output conforms to the CartoGraph
representation vocabulary `cg:` and provenance vocabulary `cgp:`.

Mapping, in one table:

    JSON                        RDF
    map                         cg:MapImage + cgp:MapConfiguration
    elements[]                  cg:PointElement / cg:LineElement /
                                cg:PolygonElement  (by anchor_dimension),
                                or a bare cg:CandidateElement when the
                                occurrence left no visible pixel
    elements[].portrayal        cg:StrokePortrayal / cg:FillPortrayal /
                                cg:TextPortrayal / cg:MarkerPortrayal /
                                cg:DotPortrayal / cg:PatternPortrayal /
                                cg:RasterPortrayal / cg:ShieldPortrayal
    objects[]                   cg:VisibleObject
    objects[].identity          geo:Feature identity + cg:featureId
    objects[].visible_properties  cg:visibleLabel  (image-grounded only)
    objects[].source_properties   predicates in the data namespace, on the
                                  geo:Feature - never on the VisibleObject

The visible/source split is preserved on purpose: everything hanging off a
cg:VisibleObject is grounded in the image, everything hanging off the
geo:Feature is source metadata.

Pixel coordinates are canvas coordinates with the origin at the top-left and y
growing downwards, which is what the renderer produces.
"""

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote, urljoin

try:
    from rdflib import Graph, Literal, Namespace, URIRef, BNode
    from rdflib.namespace import DCTERMS, OWL, RDF, RDFS, XSD
except ImportError:
    sys.exit("this tool needs rdflib: pip install rdflib")

CG = Namespace("https://w3id.org/cartograph#")
CGP = Namespace("https://w3id.org/cartograph-provenance#")
GEO = Namespace("http://www.opengis.net/ont/geosparql#")
PROV = Namespace("http://www.w3.org/ns/prov#")

CRS84 = "<http://www.opengis.net/def/crs/OGC/1.3/CRS84>"
OSM = "https://www.openstreetmap.org/"
WIKIDATA = "http://www.wikidata.org/entity/"
WEB_MERCATOR_RADIUS = 6378137.0
# Ground resolution in metres per pixel at zoom 0 for a 256 px tile.
ZOOM0_RESOLUTION = 2 * math.pi * WEB_MERCATOR_RADIUS / 256.0
# OGC standardized rendering pixel size, 0.28 mm.
OGC_PIXEL_METRES = 0.00028

ELEMENT_CLASS = {0: CG.PointElement, 1: CG.LineElement, 2: CG.PolygonElement}
VISIBILITY_STATUS = {
    "rendered_complete": CG.RenderedComplete,
    "rendered_clipped": CG.RenderedClipped,
    "occluded": CG.Occluded,
    "outside_viewport": CG.OutsideViewport,
    "suppressed": CG.Suppressed,
}
ANCHOR_CONCEPT = {0: CG.PointAnchor, 1: CG.CurveAnchor, 2: CG.SurfaceAnchor}

PORTRAYAL_CLASS = {
    "stroke": CG.StrokePortrayal,
    "fill": CG.FillPortrayal,
    "text": CG.TextPortrayal,
    "marker": CG.MarkerPortrayal,
    "dot": CG.DotPortrayal,
    "pattern": CG.PatternPortrayal,
    "raster": CG.RasterPortrayal,
    "shield": CG.ShieldPortrayal,
    "compound": CG.CompoundPortrayal,
}

# portrayal key -> (predicate, datatype)
PORTRAYAL_PROPERTY = {
    "stroke_color": (CG.strokeColor, XSD.string),
    "stroke_width_px": (CG.strokeWidthPx, XSD.decimal),
    "dash_pattern": (CG.dashPattern, XSD.string),
    "fill_color": (CG.fillColor, XSD.string),
    "opacity": (CG.opacity, XSD.decimal),
    "font_family": (CG.fontFamily, XSD.string),
    "font_size_px": (CG.fontSizePx, XSD.decimal),
    "text_color": (CG.textColor, XSD.string),
    "halo_color": (CG.haloColor, XSD.string),
    "halo_radius_px": (CG.haloRadiusPx, XSD.decimal),
    "icon_name": (CG.iconName, XSD.string),
    "pattern_name": (CG.patternName, XSD.string),
    "symbol_shape": (CG.symbolShape, XSD.string),
    "size_px": (CG.sizePx, XSD.decimal),
}


def text(value):
    """A plain RDF 1.1 string literal.

    Written without an explicit ^^xsd:string: the two are the same term in RDF
    1.1 and SHACL treats them alike, but rdflib's graph matching does not, so a
    query written with a bare "roads" silently returns nothing against a typed
    literal. Plain literals keep hand-written SPARQL working.
    """
    return Literal(str(value))


def decimal(value):
    """xsd:decimal, which SHACL asks for and a bare Python float does not give."""
    return Literal(repr(float(value)), datatype=XSD.decimal)


def slug(value):
    return quote(re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)), safe="._-")


def mercator_to_lonlat(x, y):
    lon = x / WEB_MERCATOR_RADIUS * 180.0 / math.pi
    lat = (2.0 * math.atan(math.exp(y / WEB_MERCATOR_RADIUS)) - math.pi / 2.0) * 180.0 / math.pi
    return lon, lat


def is_web_mercator(crs):
    """Recognise Web Mercator by code or by proj4 string.

    Stylesheets often carry the full proj4 definition rather than an EPSG code -
    openstreetmap-carto does - and the viewport and scale can only be derived
    when the projection is actually known.
    """
    normalized = crs.lower().replace(" ", "")
    if normalized in {"epsg:3857", "epsg:900913", "epsg:3785", "urn:ogc:def:crs:epsg::3857"}:
        return True
    return "+proj=merc" in normalized and "+a=6378137" in normalized and "+b=6378137" in normalized


def viewport_wkt(extent, crs, extent_wgs84=None):
    """CRS84 envelope polygon of the viewport, or None if we cannot compute it.

    The renderer reprojects the viewport itself when it can, which is the only
    way to get a viewport for a CRS this script does not know how to unproject
    (UTM, for instance). Prefer that answer whenever it is present.
    """
    if extent_wgs84:
        minx, miny, maxx, maxy = extent_wgs84
    else:
        minx, miny, maxx, maxy = extent
        if is_web_mercator(crs):
            (minx, miny), (maxx, maxy) = mercator_to_lonlat(minx, miny), mercator_to_lonlat(maxx, maxy)
        elif crs.lower() not in {"epsg:4326", "crs84", "urn:ogc:def:crs:ogc:1.3:crs84"}:
            return None
    ring = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy), (minx, miny)]
    coords = ", ".join("{:.6f} {:.6f}".format(x, y) for x, y in ring)
    return "{} POLYGON (({}))".format(CRS84, coords)


def scale_denominator(extent, width, crs):
    """Representative-fraction denominator, for projected (metre) CRSs only.

    This is the scale on the projection plane. Web Mercator stretches by
    1/cos(latitude), so the true ground scale is finer than this away from the
    equator; the number is left uncorrected to match cg:zoomLevel, which is
    defined the same way.
    """
    if not is_web_mercator(crs) or width <= 0:
        return None
    metres_per_pixel = (extent[2] - extent[0]) / float(width)
    if metres_per_pixel <= 0:
        return None
    return metres_per_pixel / OGC_PIXEL_METRES


def slippy_zoom(extent, width, crs):
    """Slippy-map zoom implied by the extent and canvas width, if meaningful."""
    if not is_web_mercator(crs) or width <= 0:
        return None
    metres_per_pixel = (extent[2] - extent[0]) / float(width)
    if metres_per_pixel <= 0:
        return None
    return math.log2(ZOOM0_RESOLUTION / metres_per_pixel)


def geometry_wkt(multipolygon):
    """GeoJSON MultiPolygon (pixel coordinates) as a WKT string."""
    if not multipolygon:
        return None
    parts = []
    for polygon in multipolygon["coordinates"]:
        rings = ", ".join(
            "(" + ", ".join("{:g} {:g}".format(p[0], p[1]) for p in ring) + ")" for ring in polygon
        )
        parts.append("(" + rings + ")")
    return "MULTIPOLYGON (" + ", ".join(parts) + ")"


def bbox_string(box):
    return "[{:g}, {:g}, {:g}, {:g}]".format(*box)


def load_source_geometries(directory):
    """osm_id -> WKT, read from the GeoJSON files the extract was rendered from.

    The ground truth deliberately does not carry source geometry, but the
    ontology's SHACL shapes require every geo:Feature to have one, so it is
    joined back in here when the source files are available.
    """
    geometries = {}
    if not directory:
        return geometries
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".geojson"):
            continue
        with open(os.path.join(directory, name)) as handle:
            collection = json.load(handle)
        for feature in collection.get("features", []):
            osm_id = feature.get("properties", {}).get("osm_id")
            geometry = feature.get("geometry")
            if osm_id is None or not geometry:
                continue
            wkt = geojson_to_wkt(geometry)
            if wkt:
                geometries[str(osm_id)] = wkt
    return geometries


def geojson_to_wkt(geometry):
    kind = geometry.get("type")
    coords = geometry.get("coordinates")
    fmt = "{:.7f} {:.7f}".format
    if kind == "Point":
        return "POINT ({})".format(fmt(*coords))
    if kind == "LineString":
        return "LINESTRING ({})".format(", ".join(fmt(*p) for p in coords))
    if kind == "Polygon":
        rings = ", ".join("(" + ", ".join(fmt(*p) for p in ring) + ")" for ring in coords)
        return "POLYGON ({})".format(rings)
    return None


class Converter:
    # Individuals are minted under this placeholder and then written as
    # relative IRIs, so the document can be stored anywhere - a Solid pod, a
    # different folder - and still describe itself.
    SELF = "https://maptrace.invalid/self"

    def __init__(self, truth, base, geometry_format="wkt", source_geometries=None, zoom=None,
                 include_candidates=True, infer_cased_lines=False, relative="fragment", image_url=None):
        self.truth = truth
        self.image_url = image_url
        # Two namespaces on purpose:
        #   terms  - predicates and feature classes. Absolute and stable: a
        #            vocabulary that moved with the file would be no vocabulary.
        #   data   - the individuals. Relative, so they travel with the document.
        self.relative = relative
        self.terms = Namespace(base if base.endswith(("/", "#")) else base + "/")
        if relative == "none":
            self.data = self.terms
        elif relative == "fragment":
            self.data = Namespace(self.SELF + "#")
        else:
            self.data = Namespace(self.SELF + "/")
        self.geometry_format = geometry_format
        self.source_geometries = source_geometries or {}
        self.zoom_override = zoom
        self.include_candidates = include_candidates
        self.infer_cased_lines = infer_cased_lines
        self.portrayal_of = {}
        self.graph = Graph()
        self.graph.bind("cg", CG)
        self.graph.bind("cgp", CGP)
        self.graph.bind("geo", GEO)
        self.graph.bind("prov", PROV)
        self.graph.bind("dct", DCTERMS)
        self.graph.bind("owl", OWL)
        self.graph.bind("wd", WIKIDATA)
        self.graph.bind("osm", OSM)
        self.graph.bind("ex", self.terms)
        if relative != "none":
            self.graph.bind("self", self.data)
        self.map_uri = self.data["map"]
        self.render_activity = self.data["activity/render"]
        self.layers = {}
        self.features = {}

    # -- helpers ---------------------------------------------------------
    def add(self, s, p, o):
        self.graph.add((s, p, o))

    def layer_uri(self, name, order):
        if name not in self.layers:
            uri = self.data["layer/" + slug(name or "default")]
            self.add(uri, RDF.type, CG.MapLayer)
            self.add(uri, CG.layerName, text(name))
            self.add(uri, CG.layerOrder, Literal(order, datatype=XSD.integer))
            self.add(uri, CG.inMap, self.map_uri)
            self.layers[name] = uri
        return self.layers[name]

    def feature_uri(self, layer, feature_id):
        """Feature identity is (layer, feature_id): ids repeat across layers."""
        key = (layer, feature_id)
        if key not in self.features:
            self.features[key] = self.data["feature/{}/{}".format(slug(layer), slug(feature_id))]
        return self.features[key]

    # -- conversion ------------------------------------------------------
    def convert(self):
        self.emit_provenance()
        self.emit_map()
        for obj in self.truth.get("objects", []):
            self.emit_feature(obj)
        for element in self.truth.get("elements", []):
            status = element.get("visibility_status", "rendered_complete")
            if status not in ("rendered_complete", "rendered_clipped") and not self.include_candidates:
                continue
            self.emit_element(element)
        for obj in self.truth.get("objects", []):
            self.emit_visible_object(obj)
        if self.infer_cased_lines:
            self.emit_cased_lines()
        return self.graph

    def emit_cased_lines(self):
        """Group a road's casing and fill strokes into a cg:CasedLinePortrayal.

        Mapnik has no notion of "casing": it is two line symbolizers on one
        rule, the second narrower and drawn over the first. That pattern is
        recognised here - consecutive stroke elements of one visible object
        whose width strictly decreases - so the grouping is a documented
        heuristic, not something the renderer reported.
        """
        by_id = {element["id"]: element for element in self.truth.get("elements", [])}
        for obj in self.truth.get("objects", []):
            strokes = [
                by_id[i] for i in obj.get("displayed_elements", [])
                if i in by_id and (by_id[i].get("portrayal") or {}).get("kind") == "stroke"
            ]
            strokes.sort(key=lambda e: e["sequence"])
            for first, second in zip(strokes, strokes[1:]):
                casing = first["portrayal"].get("stroke_width_px")
                fill = second["portrayal"].get("stroke_width_px")
                if casing is None or fill is None or fill >= casing:
                    continue
                uri = self.data["cased-line/{}".format(first["id"])]
                self.add(uri, RDF.type, CG.CasedLinePortrayal)
                self.add(uri, RDFS.comment,
                         Literal("Casing and fill are separate display elements; this grouping is inferred."))
                self.add(uri, CG.hasCasingStroke, self.portrayal_of[first["id"]])
                self.add(uri, CG.hasFillStroke, self.portrayal_of[second["id"]])
                self.add(uri, CG.hasComponent, self.portrayal_of[first["id"]])
                self.add(uri, CG.hasComponent, self.portrayal_of[second["id"]])

    def emit_provenance(self):
        agent = self.data["agent/mapnik-agg"]
        self.add(agent, RDF.type, PROV.Agent)
        self.add(agent, RDF.type, PROV.SoftwareAgent)
        self.add(agent, RDFS.label, Literal("Mapnik AGG renderer with visual ground-truth extraction"))

        self.add(self.render_activity, RDF.type, CGP.MapRenderingActivity)
        self.add(self.render_activity, RDF.type, PROV.Activity)
        self.add(self.render_activity, PROV.wasAssociatedWith, agent)

        config = self.data["configuration"]
        info = self.truth["map"]
        self.add(config, RDF.type, CGP.MapConfiguration)
        self.add(config, RDF.type, PROV.Entity)
        self.add(config, RDFS.label, Literal("Render configuration"))
        # cg:crs, cg:zoomLevel and cg:viewportWkt all declare rdfs:domain
        # cg:MapImage, so under RDFS reasoning this configuration is inferred to
        # be a MapImage and then has to satisfy cg:MapImageShape. The target
        # output dimensions belong on the configuration anyway, so stating them
        # here keeps the graph valid with and without a reasoner.
        self.add(config, CG.canvasWidthPx, Literal(info["width"], datatype=XSD.integer))
        self.add(config, CG.canvasHeightPx, Literal(info["height"], datatype=XSD.integer))
        self.add(config, CG.crs, text(info["crs"]))
        zoom = self.zoom_level()
        if zoom is not None:
            self.add(config, CG.zoomLevel, decimal(zoom))
        scale = scale_denominator(info["extent"], info["width"], info["crs"])
        if scale is not None:
            self.add(config, CG.scaleDenominator, decimal(scale))
        wkt = viewport_wkt(info["extent"], info["crs"], info.get("extent_wgs84"))
        if wkt:
            self.add(config, CG.viewportWkt, Literal(wkt, datatype=GEO.wktLiteral))
        self.add(self.render_activity, PROV.used, config)
        self.config = config

    def zoom_level(self):
        if self.zoom_override is not None:
            return self.zoom_override
        info = self.truth["map"]
        return slippy_zoom(info["extent"], info["width"], info["crs"])

    def emit_map(self):
        info = self.truth["map"]
        coverage = self.truth.get("coverage")
        if coverage is not None:
            self.add(self.map_uri, self.terms.complete, Literal(bool(coverage.get("complete", False))))
            self.add(self.map_uri, self.terms.coverageReport, Literal(json.dumps(coverage, sort_keys=True)))
        self.add(self.map_uri, RDF.type, CG.MapImage)
        self.add(self.map_uri, RDF.type, PROV.Entity)
        if self.image_url is not None:
            self.add(self.map_uri, CG.imageUrl, URIRef(self.image_url))
        self.add(self.map_uri, CG.canvasWidthPx, Literal(info["width"], datatype=XSD.integer))
        self.add(self.map_uri, CG.canvasHeightPx, Literal(info["height"], datatype=XSD.integer))
        self.add(self.map_uri, CG.crs, text(info["crs"]))
        zoom = self.zoom_level()
        if zoom is not None:
            self.add(self.map_uri, CG.zoomLevel, decimal(zoom))
        wkt = viewport_wkt(info["extent"], info["crs"], info.get("extent_wgs84"))
        if wkt:
            self.add(self.map_uri, CG.viewportWkt, Literal(wkt, datatype=GEO.wktLiteral))
        scale = scale_denominator(info["extent"], info["width"], info["crs"])
        if scale is not None:
            self.add(self.map_uri, CG.scaleDenominator, decimal(scale))
        if info.get("background_color"):
            self.add(self.map_uri, CG.backgroundColor, text(info["background_color"]))
        self.add(self.map_uri, CGP.usedConfiguration, self.config)
        self.add(self.map_uri, PROV.wasGeneratedBy, self.render_activity)

    def emit_feature(self, obj):
        identity = obj["identity"]
        layer = identity.get("layer", "")
        feature = self.feature_uri(layer, identity["feature_id"])
        self.add(feature, RDF.type, GEO.Feature)
        # The vocabulary has no term for "what kind of thing is this feature",
        # so - as the ontology's own examples do - a domain class is minted in
        # the data namespace from the stylesheet layer.
        self.add(feature, RDF.type, self.terms[slug(layer).capitalize() + "Feature"])
        self.add(feature, CG.featureId, text(identity["feature_id"]))

        sources = obj.get("source_properties", {}).get("source_uris", [])
        if isinstance(sources, str):
            sources = json.loads(sources)
        if not isinstance(sources, list):
            raise ValueError("source_uris must be a JSON array of absolute source IRIs")
        for source in sources:
            if not isinstance(source, str) or not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", source):
                raise ValueError("source_uris contains a non-absolute IRI")
            self.add(feature, PROV.wasDerivedFrom, URIRef(source))
            self.add(self.map_uri, PROV.wasDerivedFrom, URIRef(source))
            geometry = self.truth.get("source_geometries", {}).get(source)
            if geometry:
                import hashlib
                node = self.data["source-geometry-" + hashlib.sha256(source.encode()).hexdigest()[:20]]
                crs = geometry["crs"]
                if re.fullmatch(r"EPSG:\d+", crs):
                    crs = "http://www.opengis.net/def/crs/EPSG/0/" + crs.split(":")[1]
                elif crs == "OGC:CRS84":
                    crs = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
                self.add(URIRef(source), GEO.hasGeometry, node)
                self.add(node, RDF.type, GEO.Geometry)
                self.add(node, GEO.asWKT, Literal("<{}> {}".format(crs, geometry["wkt"]), datatype=GEO.wktLiteral))

        # Identity metadata is non-visual by definition; it lives on the source
        # feature, never on the visible object.
        for key, value in identity.items():
            if key in ("layer", "feature_id") or value is None:
                continue
            self.add(feature, self.terms[slug(key)], Literal(value))

        # Real links, once enrich_identity.py has resolved what the id refers to.
        # An osm_id alone identifies nothing: the same integer exists as a node,
        # a way and a relation, so the element type is what makes the link work.
        osm_type, osm_id = identity.get("osm_type"), identity.get("osm_id")
        if osm_type and osm_id is not None:
            self.add(feature, RDFS.seeAlso,
                     URIRef("{}{}/{}".format(OSM, osm_type, abs(int(osm_id)))))
        qid = identity.get("wikidata")
        if qid and re.fullmatch(r"Q\d+", str(qid)):
            # The map feature and the Wikidata entity are the same thing; this
            # is how OSM-to-Wikidata links are usually expressed.
            self.add(feature, OWL.sameAs, URIRef(WIKIDATA + str(qid)))
        # Source-only attributes, likewise never presented as visible. A null
        # attribute means the datasource had no value, which is not a value.
        for key, value in obj.get("source_properties", {}).items():
            if key == "source_uris":
                continue
            if value is None:
                continue
            self.add(feature, self.terms[slug(key)], Literal(value))

        name = obj.get("source_properties", {}).get("name")
        self.add(feature, RDFS.label, Literal(name if name else "{} {}".format(layer, identity["feature_id"])))

        wkt = self.source_geometries.get(str(identity.get("osm_id")))
        if wkt:
            geometry = self.data["geometry/{}/{}".format(slug(layer), slug(identity["feature_id"]))]
            self.add(geometry, RDF.type, GEO.Geometry)
            self.add(geometry, GEO.asWKT, Literal("{} {}".format(CRS84, wkt), datatype=GEO.wktLiteral))
            self.add(feature, GEO.hasGeometry, geometry)

    def emit_element(self, element):
        uri = self.data["element/{}".format(element["id"])]
        layer = element.get("layer", "")
        feature = self.feature_uri(layer, element["feature_id"])
        dimension = element.get("anchor_dimension", 2)
        status = element.get("visibility_status", "rendered_complete")
        survived = status in ("rendered_complete", "rendered_clipped")

        # A candidate that left no pixel is not a DisplayElement: it is only a
        # cg:CandidateElement, which is exactly what lets the graph record why
        # something is absent without ever claiming it is visible.
        self.add(uri, RDF.type, ELEMENT_CLASS.get(dimension, CG.PolygonElement) if survived
                 else CG.CandidateElement)
        self.add(uri, RDFS.label, Literal("{} element #{}".format(element["type"], element["id"])))
        self.add(uri, CG.inMap, self.map_uri)
        self.add(uri, CG.inLayer, self.layer_uri(layer, len(self.layers) * 10))
        self.add(uri, CG.depictsFeature, feature)
        self.add(uri, PROV.wasGeneratedBy, self.render_activity)
        self.add(uri, CG.drawOrder, Literal(element["sequence"], datatype=XSD.integer))
        self.add(uri, CG.pixelCount, Literal(element["pixel_count"], datatype=XSD.integer))
        self.add(feature, RDF.type, GEO.Feature)
        self.add(uri, CG.isClipped, Literal(bool(element["clipped"]), datatype=XSD.boolean))
        self.add(uri, CG.anchorDimension, Literal(dimension, datatype=XSD.integer))
        self.add(uri, CG.hasAnchorDimension, ANCHOR_CONCEPT[dimension])
        if element.get("placement"):
            self.add(uri, CG.placement, text(element["placement"]))
        self.add(uri, CG.visibilityStatus, VISIBILITY_STATUS.get(status, CG.RenderedComplete))

        box = element.get("pixel_bbox")
        if box:
            self.add(uri, CG.pixelBbox, text(bbox_string(box)))
            for predicate, value in zip(
                (CG.pixelBboxMinX, CG.pixelBboxMinY, CG.pixelBboxMaxX, CG.pixelBboxMaxY), box
            ):
                self.add(uri, predicate, decimal(value))
            if dimension == 0:
                self.add(uri, CG.pixelAnchorX, decimal((box[0] + box[2]) / 2.0))
                self.add(uri, CG.pixelAnchorY, decimal((box[1] + box[3]) / 2.0))

        geometry = element.get("pixel_geometry")
        if geometry:
            if self.geometry_format == "geojson":
                self.add(uri, CG.pixelGeometry, text(json.dumps(geometry)))
            else:
                self.add(uri, CG.pixelGeometry, text(geometry_wkt(geometry)))

        self.portrayal_of[element["id"]] = self.emit_portrayal(uri, element)

    def emit_portrayal(self, element_uri, element):
        portrayal = element.get("portrayal") or {}
        kind = portrayal.get("kind", "compound")
        uri = self.data["portrayal/{}".format(element["id"])]
        self.add(uri, RDF.type, PORTRAYAL_CLASS.get(kind, CG.Portrayal))
        self.add(element_uri, CG.hasPortrayal, uri)
        self.portrayal_kind = kind

        if kind in ("text", "shield") and element.get("rendered_text") is not None:
            self.add(uri if kind == "text" else uri, CG.renderedText,
                     text(element["rendered_text"]))

        for key, value in portrayal.items():
            if key == "kind":
                continue
            mapping = PORTRAYAL_PROPERTY.get(key)
            if not mapping:
                continue
            predicate, datatype = mapping
            if predicate == CG.iconName:
                # Mapnik resolves file= to an absolute path; the logical asset
                # name is what belongs in a published graph.
                value = os.path.basename(str(value))
            literal = decimal(value) if datatype == XSD.decimal else text(value)
            self.add(uri, predicate, literal)

        if kind == "shield":
            # A shield is one occurrence drawn as an icon plus its text, so the
            # compound carries both components.
            icon = BNode()
            self.add(icon, RDF.type, CG.MarkerPortrayal)
            self.add(icon, CG.iconName, text(portrayal.get("icon_name", "shield")))
            text_component = BNode()
            self.add(text_component, RDF.type, CG.TextPortrayal)
            self.add(text_component, CG.renderedText, text(element.get("rendered_text", "")))
            if "font_size_px" in portrayal:
                self.add(text_component, CG.fontSizePx, decimal(portrayal["font_size_px"]))
            self.add(uri, CG.hasIconComponent, icon)
            self.add(uri, CG.hasTextComponent, text_component)
            self.add(uri, CG.hasComponent, icon)
            self.add(uri, CG.hasComponent, text_component)
        return uri

    def emit_visible_object(self, obj):
        identity = obj["identity"]
        layer = identity.get("layer", "")
        uri = self.data["visible-object/{}/{}".format(slug(layer), slug(identity["feature_id"]))]
        feature = self.feature_uri(layer, identity["feature_id"])

        self.add(uri, RDF.type, CG.VisibleObject)
        self.add(uri, RDF.type, PROV.Entity)
        self.add(uri, RDFS.label, Literal("Visible {} {}".format(layer, identity["feature_id"])))
        self.add(uri, CG.inMap, self.map_uri)
        self.add(uri, CG.inLayer, self.layer_uri(layer, len(self.layers) * 10))
        self.add(uri, CG.representsFeature, feature)
        self.add(uri, CG.featureId, text(identity["feature_id"]))
        self.add(uri, CG.visiblePixelCount, Literal(obj.get("visible_pixel_count", 0), datatype=XSD.integer))

        # Only image-grounded values become visible properties.
        label = obj.get("visible_properties", {}).get("label")
        if label:
            self.add(uri, CG.visibleLabel, text(label))

        box = obj.get("visible_pixel_bbox")
        if box:
            self.add(uri, CG.visiblePixelBbox, text(bbox_string(box)))
        geometry = obj.get("visible_geometry")
        if geometry and self.geometry_format != "none":
            value = json.dumps(geometry) if self.geometry_format == "geojson" else geometry_wkt(geometry)
            self.add(uri, CG.pixelGeometry, text(value))

        for element_id in obj.get("displayed_elements", []):
            element = self.data["element/{}".format(element_id)]
            self.add(uri, CG.hasDisplayedElement, element)
            self.add(element, CG.partOfVisibleObject, uri)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ground_truth", help="JSON from mapnik-ground-truth-render")
    parser.add_argument("-o", "--output", default=None, help="output file (default: alongside the input)")
    parser.add_argument("--base", default="https://example.org/maptrace/terms/",
                        help="stable namespace for attribute predicates and feature classes")
    parser.add_argument("--relative", choices=["fragment", "path", "none"], default="fragment",
                        help="how the individuals are named: document-relative fragments (default, "
                             "self-contained and portable), document-relative paths, or absolute "
                             "URIs under --base")
    parser.add_argument("--format", default="turtle", help="rdflib serialization (turtle, nt, json-ld, ...)")
    parser.add_argument("--geometry-format", choices=("wkt", "geojson", "none"), default="wkt",
                        help="how cg:pixelGeometry is written")
    parser.add_argument("--source-geojson", default=None,
                        help="directory of the GeoJSON the map was rendered from, to attach geo:hasGeometry")
    parser.add_argument("--image-url", default=None,
                        help="image IRI for cg:imageUrl; relative to the RDF document (default: "
                             "the PNG alongside the input JSON, relative to the output file)")
    parser.add_argument("--zoom", type=float, default=None, help="override the derived zoom level")
    parser.add_argument("--no-candidates", action="store_true",
                        help="skip occurrences that left no visible pixel instead of recording them "
                             "as cg:CandidateElement")
    parser.add_argument("--infer-cased-lines", action="store_true",
                        help="group consecutive narrowing strokes into cg:CasedLinePortrayal (heuristic)")
    opts = parser.parse_args()

    with open(opts.ground_truth) as handle:
        truth = json.load(handle)
    if "elements" not in truth:
        sys.exit("this ground truth has no per-element data; re-render with a current mapnik-ground-truth-render")

    suffix = {"turtle": ".ttl", "nt": ".nt", "json-ld": ".jsonld", "xml": ".rdf"}.get(opts.format, ".rdf")
    output = opts.output or os.path.splitext(opts.ground_truth)[0] + suffix
    image_url = opts.image_url
    if image_url is None:
        image_path = os.path.splitext(os.path.abspath(opts.ground_truth))[0] + ".png"
        image_url = quote(os.path.relpath(image_path, os.path.dirname(os.path.abspath(output))).replace(os.sep, "/"),
                          safe="/")
    if opts.format != "turtle":
        # Formats such as N-Triples require absolute IRIs. Turtle keeps the
        # document-relative reference so PNG and RDF can move together.
        image_url = urljoin(Path(output).absolute().as_uri(), image_url)

    converter = Converter(
        truth,
        base=opts.base,
        geometry_format=opts.geometry_format,
        source_geometries=load_source_geometries(opts.source_geojson),
        zoom=opts.zoom,
        include_candidates=not opts.no_candidates,
        infer_cased_lines=opts.infer_cased_lines,
        relative=opts.relative,
        image_url=image_url,
    )
    graph = converter.convert()

    if opts.relative == "none" or opts.format != "turtle":
        graph.serialize(destination=output, format=opts.format)
    else:
        text = graph.serialize(format="turtle", base=Converter.SELF + (
            "#" if opts.relative == "fragment" else "/"))
        # rdflib writes an @base and does not relativise fragments itself; both
        # are undone here so nothing in the file names its own location.
        lines = [l for l in text.splitlines() if not l.startswith("@base")]
        text = "\n".join(lines)
        text = text.replace("<{}#".format(Converter.SELF), "<#")
        text = text.replace("<{}/".format(Converter.SELF), "<")
        with open(output, "w") as handle:
            handle.write(text)

    print("wrote {} - {} triples".format(output, len(graph)))
    survivors = [e for e in truth.get("elements", [])
                 if e.get("visibility_status", "rendered_complete") in ("rendered_complete", "rendered_clipped")]
    print("  {:5} display elements".format(len(survivors)))
    print("  {:5} non-surviving candidates".format(len(truth.get("elements", [])) - len(survivors)))
    print("  {:5} visible objects".format(len(truth.get("objects", []))))
    print("  {:5} features with source geometry".format(len(converter.source_geometries) + len(truth.get("source_geometries", {}))))


if __name__ == "__main__":
    main()
