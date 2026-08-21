/*****************************************************************************
 *
 * This file is part of Mapnik (c++ mapping toolkit)
 *
 * Copyright (C) 2025 Artem Pavlenko
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public
 * License as published by the Free Software Foundation; either
 * version 2.1 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU Lesser General Public
 * License along with this library; if not, write to the Free Software
 * Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
 *
 *****************************************************************************/

#ifndef MAPNIK_RENDER_OBSERVER_HPP
#define MAPNIK_RENDER_OBSERVER_HPP

// mapnik
#include <mapnik/config.hpp>
#include <mapnik/value/types.hpp>
#include <mapnik/value.hpp>
#include <mapnik/attribute.hpp>
#include <mapnik/geometry/box2d.hpp>
#include <mapnik/geometry/polygon.hpp>
#include <mapnik/geometry/multi_polygon.hpp>

// stl
#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <vector>

namespace mapnik {

class feature_impl;
class layer;
class Map;
struct symbolizer_base;

// Identity of one concrete graphical occurrence. Ids are assigned by the
// renderer, start at 1 and increase monotonically in render order, so the id
// doubles as the render sequence. 0 is reserved for "no element".
using element_id = std::uint64_t;

// One kind of graphical occurrence, following the symbolizers supported by the
// AGG renderer.
enum class display_element_type : std::uint8_t {
    point,
    line,
    line_pattern,
    polygon,
    polygon_pattern,
    raster,
    text,
    shield,
    building,
    markers,
    group,
    dot,
    debug
};

MAPNIK_DECL char const* to_string(display_element_type type);

// What the whole pass is about: enough to describe the canvas an element's
// pixel coordinates refer to.
struct map_info
{
    unsigned width;
    unsigned height;
    std::string srs;
    box2d<double> extent;              // in the map's own srs
    std::string background_color;      // empty when the map has none
    double scale_factor;
};

// Callback-scoped description of a rendering occurrence. The layer, feature and
// symbolizer references are only valid for the duration of the callback;
// observers must copy anything they retain.
struct render_event
{
    element_id id;
    std::uint64_t sequence; // == id, kept separate for readability
    layer const* layer_;    // null only if rendering outside any layer
    feature_impl const& feature;
    symbolizer_base const& symbolizer;
    display_element_type type;
    // Text actually laid out for this occurrence (text/shield only), otherwise null.
    value rendered_text;
    // Render-time variables, needed to evaluate the symbolizer's own styling.
    attributes const* variables;
};

// A rendering occurrence that survived final visibility resolution.
struct element_visibility
{
    element_id id;
    // Bounding box of the element's *final* visible pixels, in output pixel
    // coordinates, half-open: a single visible pixel (x, y) yields
    // box2d(x, y, x + 1, y + 1).
    box2d<double> pixel_bbox;
    // Number of final output pixels the element still owns.
    std::uint64_t pixel_count;
    // Outline of those pixels, in the same coordinates: the exact visible
    // shape, with holes, rather than its extent. A road keeps its course and a
    // partly covered lake keeps its remaining outline.
    geometry::multi_polygon<double> pixel_geometry;
};

// Owning result: one concrete graphical occurrence that contributes to at least
// one pixel of the final image.
struct displayed_element
{
    element_id id;
    std::uint64_t sequence;
    std::string layer_name;
    value_integer feature_id;
    display_element_type type;
    std::optional<box2d<double>> pixel_bbox;
    std::uint64_t pixel_count = 0;
    // Pixels this occurrence painted when it was drawn, before later elements
    // covered any of them. Always >= pixel_count.
    std::uint64_t drawn_pixel_count = 0;
    geometry::multi_polygon<double> pixel_geometry;
    value rendered_text;

    // Topological dimension of the canvas primitive: 0 point, 1 line, 2 area.
    int anchor_dimension = 2;
    // Cartographic placement mode: "point", "line" or "area".
    std::string placement;
    // True when the visible pixels touch the canvas border, i.e. the element is
    // cut off by the viewport rather than ending inside it.
    bool clipped = false;

    // How this occurrence was styled: a kind ("stroke", "fill", "text",
    // "marker", "dot", "pattern", "raster", "shield") and the styling values
    // that were actually in effect for this feature.
    std::string portrayal_kind;
    std::map<std::string, value> portrayal;
};

// Owning result: a source feature with at least one surviving displayed element.
struct visible_object
{
    std::string layer_name;
    value_integer feature_id;

    // Non-visual identity information used to connect this object back to
    // source data. Never implies that the value is readable in the image.
    std::map<std::string, value> identity_metadata;

    // Information actually manifested in the image.
    std::map<std::string, value> visible_properties;

    // Datasource attributes that are *not* visually expressed. Populated only
    // when the observer requested full attribute collection. Must never be
    // merged into visible_properties.
    std::map<std::string, value> source_properties;

    std::vector<element_id> displayed_element_ids;
    std::optional<box2d<double>> visible_pixel_bbox;
    std::uint64_t visible_pixel_count = 0;
    // Union of the visible outlines of this object's displayed elements.
    geometry::multi_polygon<double> visible_pixel_geometry;
};

/* Optional integration point of the AGG renderer.
 *
 * With no observer installed the renderer behaves exactly as before and
 * allocates no tracking state.
 *
 * Exceptions thrown from these callbacks propagate through
 * agg_renderer::apply() and abort rendering.
 *
 * Observers are not thread-safe; sharing one between simultaneous renders is
 * the caller's responsibility.
 */
class MAPNIK_DECL render_observer
{
  public:
    virtual ~render_observer() = default;

    // Requesting all attributes makes them available to the observer. It does
    // *not* make them visually observable.
    virtual bool requires_all_attributes() const { return false; }

    // Douglas-Peucker tolerance, in pixels, applied to traced visible outlines.
    // Anti-aliased edges trace as staircases; the default collapses them
    // without moving an edge by more than half a pixel. Return 0 for the exact
    // pixel-corner outline.
    virtual double geometry_simplify_tolerance() const { return 0.5; }

    // Called once per rendering occurrence that may contribute to output.
    // Called once before any element, describing the canvas being drawn.
    virtual void begin_map(map_info const&) {}

    virtual void begin_element(render_event const& event) = 0;
    // drawn_pixels is what this occurrence put on the canvas at the moment it
    // was drawn - before anything later covered it. Zero means it rasterized
    // nothing inside the viewport at all.
    virtual void end_element(element_id id, std::uint64_t drawn_pixels) = 0;

    // Called once at the end of the render pass with the elements that still
    // contribute to the final image, ordered by id.
    virtual void finalize(std::vector<element_visibility> const& visible) = 0;
};

} // namespace mapnik

#endif // MAPNIK_RENDER_OBSERVER_HPP
