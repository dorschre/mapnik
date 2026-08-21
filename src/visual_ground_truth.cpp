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

#include <mapnik/visual_ground_truth.hpp>
#include <mapnik/visibility_tracker.hpp>
#include <mapnik/geometry/multi_polygon.hpp>
#include <mapnik/feature.hpp>
#include <mapnik/layer.hpp>
#include <mapnik/unicode.hpp>
#include <mapnik/color.hpp>
#include <mapnik/symbolizer.hpp>
#include <mapnik/symbolizer_keys.hpp>
#include <mapnik/text/placements/base.hpp>
#include <mapnik/text/text_properties.hpp>
#include <mapnik/util/variant.hpp>

// stl
#include <algorithm>
#include <cstdio>
#include <ostream>
#include <sstream>
#include <utility>

namespace mapnik {

namespace {

void write_escaped(std::ostream& out, std::string const& text)
{
    out << '"';
    for (char const c : text)
    {
        switch (c)
        {
            case '"':
                out << "\\\"";
                break;
            case '\\':
                out << "\\\\";
                break;
            case '\n':
                out << "\\n";
                break;
            case '\r':
                out << "\\r";
                break;
            case '\t':
                out << "\\t";
                break;
            default:
                if (static_cast<unsigned char>(c) < 0x20)
                {
                    char buf[7];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", static_cast<unsigned char>(c));
                    out << buf;
                }
                else
                {
                    out << c;
                }
        }
    }
    out << '"';
}

struct json_value_writer
{
    std::ostream& out;

    void operator()(value_null const&) const { out << "null"; }
    void operator()(value_bool const& v) const { out << (v ? "true" : "false"); }
    void operator()(value_integer const& v) const { out << v; }
    void operator()(value_double const& v) const { out << v; }
    void operator()(value_unicode_string const& v) const
    {
        std::string utf8;
        to_utf8(v, utf8);
        write_escaped(out, utf8);
    }
};

void write_value(std::ostream& out, value const& v)
{
    util::apply_visitor(json_value_writer{out}, v);
}

// Writes a complete JSON object, collapsed to {} when empty.
void write_properties(std::ostream& out, std::map<std::string, value> const& props, char const* indent)
{
    if (props.empty())
    {
        out << "{}";
        return;
    }
    out << "{";
    bool first = true;
    for (auto const& entry : props)
    {
        out << (first ? "\n" : ",\n") << indent << "  ";
        write_escaped(out, entry.first);
        out << ": ";
        write_value(out, entry.second);
        first = false;
    }
    out << "\n" << indent << "}";
}

// GeoJSON-shaped MultiPolygon in output pixel coordinates.
void write_geometry(std::ostream& out, geometry::multi_polygon<double> const& geom, char const* indent)
{
    if (geom.empty())
    {
        out << "null";
        return;
    }
    out << "{\n" << indent << "  \"type\": \"MultiPolygon\",\n" << indent << "  \"coordinates\": [";
    bool first_polygon = true;
    for (auto const& polygon : geom)
    {
        out << (first_polygon ? "\n" : ",\n") << indent << "    [";
        first_polygon = false;
        bool first_ring = true;
        for (auto const& ring : polygon)
        {
            out << (first_ring ? "\n" : ",\n") << indent << "      [";
            first_ring = false;
            bool first_point = true;
            for (auto const& point : ring)
            {
                out << (first_point ? "" : ", ") << "[" << point.x << ", " << point.y << "]";
                first_point = false;
            }
            out << "]";
        }
        out << "\n" << indent << "    ]";
    }
    out << "\n" << indent << "  ]\n" << indent << "}";
}


// --- portrayal extraction -----------------------------------------------
//
// The observer sees the symbolizer that produced each occurrence, so the
// styling that was actually in effect for this feature can be read straight
// off it - including values that came from an expression.

void put_color(std::map<std::string, value>& out, char const* key, color const& c)
{
    mapnik::transcoder const tr("utf-8");
    out.emplace(key, value(tr.transcode(c.to_hex_string().c_str())));
}

void put_text(std::map<std::string, value>& out, char const* key, std::string const& text)
{
    if (text.empty())
        return;
    mapnik::transcoder const tr("utf-8");
    out.emplace(key, value(tr.transcode(text.c_str())));
}

// Reads one of the text format properties, which are held as unevaluated
// symbolizer values rather than plain numbers.
double format_double(symbolizer_base::value_type const& v, double fallback)
{
    if (v.is<value_double>())
        return v.get<value_double>();
    if (v.is<value_integer>())
        return static_cast<double>(v.get<value_integer>());
    return fallback;
}

std::optional<color> format_color(symbolizer_base::value_type const& v)
{
    if (v.is<color>())
        return v.get<color>();
    return std::nullopt;
}

void extract_text_format(std::map<std::string, value>& out,
                         symbolizer_base const& sym,
                         feature_impl const&,
                         attributes const&)
{
    auto const placements = get_optional<text_placements_ptr>(sym, keys::text_placements_);
    if (!placements || !*placements)
        return;
    auto const& format = (*placements)->defaults.format_defaults;
    put_text(out, "font_family", format.face_name);
    out.emplace("font_size_px", value(format_double(format.text_size, 10.0)));
    if (auto const fill = format_color(format.fill))
        put_color(out, "text_color", *fill);
    if (auto const halo = format_color(format.halo_fill))
        put_color(out, "halo_color", *halo);
    double const halo_radius = format_double(format.halo_radius, 0.0);
    if (halo_radius > 0.0)
        out.emplace("halo_radius_px", value(halo_radius));
}

void extract_stroke(std::map<std::string, value>& out,
                    symbolizer_base const& sym,
                    feature_impl const& feature,
                    attributes const& vars)
{
    put_color(out, "stroke_color", get<color>(sym, keys::stroke, feature, vars, color(0, 0, 0)));
    out.emplace("stroke_width_px", value(get<value_double>(sym, keys::stroke_width, feature, vars, 1.0)));
    double const opacity = get<value_double>(sym, keys::stroke_opacity, feature, vars, 1.0);
    if (opacity < 1.0)
        out.emplace("opacity", value(opacity));
    if (auto const dash = get_optional<dash_array>(sym, keys::stroke_dasharray, feature, vars))
    {
        std::string text;
        for (auto const& pair : *dash)
        {
            if (!text.empty())
                text += " ";
            text += std::to_string(pair.first) + " " + std::to_string(pair.second);
        }
        put_text(out, "dash_pattern", text);
    }
}

void extract_fill(std::map<std::string, value>& out,
                  symbolizer_base const& sym,
                  feature_impl const& feature,
                  attributes const& vars)
{
    put_color(out, "fill_color", get<color>(sym, keys::fill, feature, vars, color(128, 128, 128)));
    double const opacity = get<value_double>(sym, keys::fill_opacity, feature, vars, 1.0);
    if (opacity < 1.0)
        out.emplace("opacity", value(opacity));
}

// Fills in the element's portrayal, its anchor dimension and its placement mode.
void extract_portrayal(displayed_element& element,
                       symbolizer_base const& sym,
                       feature_impl const& feature,
                       attributes const& vars)
{
    auto& out = element.portrayal;
    switch (element.type)
    {
        case display_element_type::line:
        case display_element_type::line_pattern:
            element.portrayal_kind = "stroke";
            element.anchor_dimension = 1;
            element.placement = "line";
            extract_stroke(out, sym, feature, vars);
            break;

        case display_element_type::polygon:
        case display_element_type::building:
            element.portrayal_kind = "fill";
            element.anchor_dimension = 2;
            element.placement = "area";
            extract_fill(out, sym, feature, vars);
            break;

        case display_element_type::polygon_pattern:
            element.portrayal_kind = "pattern";
            element.anchor_dimension = 2;
            element.placement = "area";
            if (auto const file = get_optional<std::string>(sym, keys::file, feature, vars))
                put_text(out, "pattern_name", *file);
            break;

        case display_element_type::raster:
            element.portrayal_kind = "raster";
            element.anchor_dimension = 2;
            element.placement = "area";
            break;

        case display_element_type::point:
        case display_element_type::markers:
        case display_element_type::dot:
        {
            element.anchor_dimension = 0;
            element.placement = "point";
            auto const file = get_optional<std::string>(sym, keys::file, feature, vars);
            if (file && !file->empty())
            {
                element.portrayal_kind = "marker";
                put_text(out, "icon_name", *file);
            }
            else
            {
                // No image asset: mapnik draws its built-in shape.
                element.portrayal_kind = "dot";
                put_text(out, "symbol_shape", "ellipse");
                out.emplace("size_px", value(get<value_double>(sym, keys::width, feature, vars, 10.0)));
                put_color(out, "fill_color", get<color>(sym, keys::fill, feature, vars, color(0, 0, 255)));
            }
            double const opacity = get<value_double>(sym, keys::opacity, feature, vars, 1.0);
            if (opacity < 1.0)
                out.emplace("opacity", value(opacity));
            break;
        }

        case display_element_type::text:
        case display_element_type::shield:
        {
            element.portrayal_kind = (element.type == display_element_type::shield) ? "shield" : "text";
            // Label placement is a text-placement property, not a plain
            // symbolizer key, so it has to be read off the placements object.
            auto placement = label_placement_enum::POINT_PLACEMENT;
            if (auto const placements = get_optional<text_placements_ptr>(sym, keys::text_placements_))
            {
                if (*placements)
                {
                    auto const& expression = (*placements)->defaults.expressions.label_placement;
                    if (expression.is<enumeration_wrapper>())
                        placement = static_cast<label_placement_enum>(expression.get<enumeration_wrapper>().value);
                }
            }
            bool const along_line = (placement == label_placement_enum::LINE_PLACEMENT ||
                                     placement == label_placement_enum::VERTEX_PLACEMENT);
            element.anchor_dimension = along_line ? 1 : 0;
            element.placement = along_line ? "line" : "point";
            extract_text_format(out, sym, feature, vars);
            if (element.type == display_element_type::shield)
            {
                if (auto const file = get_optional<std::string>(sym, keys::file, feature, vars))
                    put_text(out, "icon_name", *file);
            }
            break;
        }

        case display_element_type::group:
        case display_element_type::debug:
            element.portrayal_kind = "compound";
            element.anchor_dimension = 2;
            element.placement = "area";
            break;
    }
}

} // namespace

void visual_ground_truth_collector::begin_map(map_info const& info)
{
    map_ = info;
}

void visual_ground_truth_collector::begin_element(render_event const& event)
{
    ++rendered_count_;
    displayed_element element;
    element.id = event.id;
    element.sequence = event.sequence;
    element.layer_name = event.layer_ ? event.layer_->name() : std::string();
    element.feature_id = event.feature.id();
    element.type = event.type;
    element.rendered_text = event.rendered_text;
    static attributes const no_variables;
    extract_portrayal(element, event.symbolizer, event.feature, event.variables ? *event.variables : no_variables);

    pending_element pending;
    pending.element = std::move(element);

    // Copy attributes once per source feature, not once per graphical
    // occurrence, and only when they were actually asked for: a datasource may
    // hand back more than was queried, and unrequested attributes should not
    // silently swell the result.
    auto const key = std::make_pair(pending.element.layer_name, pending.element.feature_id);
    if ((collect_all_attributes_ || !identity_attributes_.empty()) && attributes_.find(key) == attributes_.end())
    {
        std::map<std::string, value> attrs;
        for (auto const& kv : event.feature)
        {
            // Some datasources give every feature the union of all keys in the
            // file, with nulls for the ones it does not have. A null is not a
            // value, and carrying them makes the result mostly padding.
            if (std::get<1>(kv).is_null())
                continue;
            attrs.emplace(std::get<0>(kv), std::get<1>(kv));
        }
        attributes_.emplace(key, std::move(attrs));
    }

    pending_.push_back(std::move(pending));
}

void visual_ground_truth_collector::end_element(element_id id, std::uint64_t drawn_pixels)
{
    // Final visibility is only known once the whole pass is finished; what is
    // known now is how much this occurrence actually painted.
    if (!pending_.empty() && pending_.back().element.id == id)
        pending_.back().element.drawn_pixel_count = drawn_pixels;
}

void visual_ground_truth_collector::finalize(std::vector<element_visibility> const& visible)
{
    displayed_.clear();
    invisible_.clear();
    objects_.clear();

    // `visible` is ordered by id, as is `pending_`.
    auto it = visible.begin();
    for (auto& pending : pending_)
    {
        while (it != visible.end() && it->id < pending.element.id)
            ++it;
        if (it == visible.end() || it->id != pending.element.id)
        {
            if (keep_candidates_)
                invisible_.push_back(pending.element);
            continue;
        }
        pending.element.pixel_bbox = it->pixel_bbox;
        pending.element.pixel_count = it->pixel_count;
        pending.element.pixel_geometry = it->pixel_geometry;
        // Touching the canvas border means the viewport cut the element off
        // rather than the element ending there.
        auto const& box = it->pixel_bbox;
        pending.element.clipped = (box.minx() <= 0.0 || box.miny() <= 0.0 ||
                                   box.maxx() >= static_cast<double>(map_.width) ||
                                   box.maxy() >= static_cast<double>(map_.height));
        displayed_.push_back(pending.element);
    }

    mapnik::transcoder const tr("utf-8");
    std::map<std::pair<std::string, value_integer>, std::size_t> index;
    for (auto const& element : displayed_)
    {
        auto const key = std::make_pair(element.layer_name, element.feature_id);
        auto found = index.find(key);
        if (found == index.end())
        {
            visible_object object;
            object.layer_name = element.layer_name;
            object.feature_id = element.feature_id;
            object.identity_metadata.emplace("layer", value(tr.transcode(element.layer_name.c_str())));
            object.identity_metadata.emplace("feature_id", value(element.feature_id));

            auto const attrs = attributes_.find(key);
            if (attrs != attributes_.end())
            {
                for (auto const& attr : attrs->second)
                {
                    if (identity_attributes_.count(attr.first))
                        object.identity_metadata.emplace(attr.first, attr.second);
                    else if (collect_all_attributes_)
                        object.source_properties.emplace(attr.first, attr.second);
                }
            }
            found = index.emplace(key, objects_.size()).first;
            objects_.push_back(std::move(object));
        }

        visible_object& object = objects_[found->second];
        object.displayed_element_ids.push_back(element.id);
        if (element.pixel_bbox)
        {
            if (object.visible_pixel_bbox)
                object.visible_pixel_bbox->expand_to_include(*element.pixel_bbox);
            else
                object.visible_pixel_bbox = element.pixel_bbox;
        }
        object.visible_pixel_count += element.pixel_count;
        // The union is kept as separate parts rather than dissolved: a road's
        // casing and fill overlap, and merging them would need real polygon
        // boolean operations for no benefit to a consumer that draws or
        // measures them.
        object.visible_pixel_geometry.insert(object.visible_pixel_geometry.end(),
                                             element.pixel_geometry.begin(),
                                             element.pixel_geometry.end());
        // Only text that survived to the final image counts as a visible property.
        if (!element.rendered_text.is_null())
        {
            object.visible_properties.emplace("label", element.rendered_text);
        }
    }
    pending_.clear();
    attributes_.clear();
}

void visual_ground_truth_collector::to_json(std::ostream& out) const
{
    out << "{\n  \"map\": {";
    out << "\n    \"width\": " << map_.width;
    out << ",\n    \"height\": " << map_.height;
    out << ",\n    \"crs\": ";
    write_escaped(out, map_.srs);
    {
        // Projected coordinates need more than the default six significant digits.
        auto const previous = out.precision(15);
        out << ",\n    \"extent\": [" << map_.extent.minx() << ", " << map_.extent.miny() << ", "
            << map_.extent.maxx() << ", " << map_.extent.maxy() << "]";
        out.precision(previous);
    }
    if (!map_.background_color.empty())
    {
        out << ",\n    \"background_color\": ";
        write_escaped(out, map_.background_color);
    }
    out << ",\n    \"scale_factor\": " << map_.scale_factor;
    out << ",\n    \"rendered_element_count\": " << rendered_count_;
    out << ",\n    \"displayed_element_count\": " << displayed_.size();
    out << ",\n    \"invisible_element_count\": " << invisible_.size();
    out << "\n  },\n  \"elements\": [";

    bool first_element = true;
    auto write_element = [&](displayed_element const& element, bool visible) {
        out << (first_element ? "\n" : ",\n");
        first_element = false;
        out << "    {\n      \"id\": " << element.id;
        out << ",\n      \"sequence\": " << element.sequence;
        out << ",\n      \"type\": ";
        write_escaped(out, to_string(element.type));
        out << ",\n      \"layer\": ";
        write_escaped(out, element.layer_name);
        out << ",\n      \"feature_id\": " << element.feature_id;
        out << ",\n      \"anchor_dimension\": " << element.anchor_dimension;
        out << ",\n      \"placement\": ";
        write_escaped(out, element.placement);
        out << ",\n      \"clipped\": " << (element.clipped ? "true" : "false");
        out << ",\n      \"visibility_status\": ";
        if (visible)
            write_escaped(out, element.clipped ? "rendered_clipped" : "rendered_complete");
        else
            write_escaped(out, element.drawn_pixel_count > 0 ? "occluded" : "outside_viewport");
        out << ",\n      \"pixel_count\": " << element.pixel_count;
        out << ",\n      \"drawn_pixel_count\": " << element.drawn_pixel_count;
        if (element.pixel_bbox)
        {
            auto const& box = *element.pixel_bbox;
            out << ",\n      \"pixel_bbox\": [" << box.minx() << ", " << box.miny() << ", " << box.maxx()
                << ", " << box.maxy() << "]";
        }
        if (!element.rendered_text.is_null())
        {
            out << ",\n      \"rendered_text\": ";
            write_value(out, element.rendered_text);
        }
        out << ",\n      \"portrayal\": {\n        \"kind\": ";
        write_escaped(out, element.portrayal_kind);
        for (auto const& entry : element.portrayal)
        {
            out << ",\n        ";
            write_escaped(out, entry.first);
            out << ": ";
            write_value(out, entry.second);
        }
        out << "\n      }";
        out << ",\n      \"pixel_geometry\": ";
        write_geometry(out, element.pixel_geometry, "      ");
        out << "\n    }";
    };

    for (auto const& element : displayed_)
        write_element(element, true);
    for (auto const& element : invisible_)
        write_element(element, false);
    out << (first_element ? "],\n  \"objects\": [" : "\n  ],\n  \"objects\": [");

    bool first_object = true;
    for (auto const& object : objects_)
    {
        out << (first_object ? "\n" : ",\n");
        first_object = false;
        out << "    {\n      \"identity\": ";
        write_properties(out, object.identity_metadata, "      ");
        out << ",\n      \"visible_properties\": ";
        write_properties(out, object.visible_properties, "      ");
        out << ",\n      \"source_properties\": ";
        write_properties(out, object.source_properties, "      ");
        out << ",\n      \"displayed_elements\": [";
        bool first_id = true;
        for (auto const id : object.displayed_element_ids)
        {
            out << (first_id ? "" : ", ") << id;
            first_id = false;
        }
        out << "]";
        out << ",\n      \"visible_pixel_count\": " << object.visible_pixel_count;
        if (object.visible_pixel_bbox)
        {
            auto const& box = *object.visible_pixel_bbox;
            out << ",\n      \"visible_pixel_bbox\": [" << box.minx() << ", " << box.miny() << ", " << box.maxx()
                << ", " << box.maxy() << "]";
        }
        out << ",\n      \"visible_geometry\": ";
        write_geometry(out, object.visible_pixel_geometry, "      ");
        out << "\n    }";
    }
    out << (first_object ? "]\n}\n" : "\n  ]\n}\n");
}

std::string visual_ground_truth_collector::to_json() const
{
    std::ostringstream out;
    to_json(out);
    return out.str();
}

} // namespace mapnik
