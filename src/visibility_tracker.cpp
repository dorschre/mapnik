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

#include <mapnik/visibility_tracker.hpp>
#include <mapnik/simplify.hpp>
#include <mapnik/simplify_converter.hpp>
#include <mapnik/vertex.hpp>
#include <mapnik/vertex_adapters.hpp>

// stl
#include <algorithm>
#include <cmath>
#include <map>
#include <stdexcept>
#include <unordered_map>
#include <vector>

namespace mapnik {

namespace {

// An element id must fit the uint32 id buffer.
constexpr element_id max_element_id = 0xffffffffu;


// An element's visible pixels form an arbitrary region: a road is a thin
// diagonal band, a partly covered lake is a rectangle with a bite out of it.
// Its bounding box says almost nothing about either, so the region is traced
// into real outlines.
//
// Each visible pixel contributes the unit-square edges that face a pixel the
// element does not own. Emitted in the order below, the edges of an outer
// boundary run clockwise on screen (positive shoelace area with y pointing
// down) and the edges around a hole run the other way, so the sign of a ring's
// area tells exteriors and holes apart without any extra containment test.
struct corner
{
    std::int32_t x;
    std::int32_t y;

    bool operator==(corner const& rhs) const { return x == rhs.x && y == rhs.y; }
};

struct corner_hash
{
    std::size_t operator()(corner const& c) const
    {
        return (static_cast<std::size_t>(static_cast<std::uint32_t>(c.x)) << 32) ^
               static_cast<std::uint32_t>(c.y);
    }
};

struct boundary_edge
{
    corner from;
    corner to;
    bool used;
};

double signed_area(geometry::linear_ring<double> const& ring)
{
    double sum = 0.0;
    for (std::size_t i = 0, n = ring.size(); i + 1 < n; ++i)
    {
        sum += ring[i].x * ring[i + 1].y - ring[i + 1].x * ring[i].y;
    }
    return 0.5 * sum;
}

// Drops vertices that sit on the straight line between their neighbours; the
// staircases anti-aliasing produces are then handled by Douglas-Peucker.
geometry::linear_ring<double> drop_collinear(geometry::linear_ring<double> const& ring)
{
    if (ring.size() < 4)
        return ring;
    geometry::linear_ring<double> out;
    std::size_t const n = ring.size() - 1; // last point repeats the first
    for (std::size_t i = 0; i < n; ++i)
    {
        auto const& previous = ring[(i + n - 1) % n];
        auto const& current = ring[i];
        auto const& next = ring[(i + 1) % n];
        double const cross = (current.x - previous.x) * (next.y - previous.y) -
                             (current.y - previous.y) * (next.x - previous.x);
        if (std::fabs(cross) > 1e-12)
            out.emplace_back(current.x, current.y);
    }
    if (out.size() < 3)
        return geometry::linear_ring<double>();
    out.emplace_back(out.front().x, out.front().y);
    return out;
}

geometry::linear_ring<double> simplify_ring(geometry::linear_ring<double> const& ring, double tolerance)
{
    if (tolerance <= 0.0 || ring.size() < 5)
        return ring;
    geometry::ring_vertex_adapter<double> adapter(ring);
    simplify_converter<geometry::ring_vertex_adapter<double>> converter(adapter);
    converter.set_simplify_algorithm(simplify_algorithm_e::douglas_peucker);
    converter.set_simplify_tolerance(tolerance);
    converter.rewind(0);

    geometry::linear_ring<double> out;
    double x = 0.0;
    double y = 0.0;
    unsigned command = SEG_END;
    while ((command = converter.vertex(&x, &y)) != SEG_END)
    {
        if (command == SEG_MOVETO || command == SEG_LINETO)
            out.emplace_back(x, y);
    }
    if (out.size() < 3)
        return geometry::linear_ring<double>();
    if (out.front().x != out.back().x || out.front().y != out.back().y)
        out.emplace_back(out.front().x, out.front().y);
    return out.size() < 4 ? geometry::linear_ring<double>() : out;
}

bool point_in_ring(geometry::linear_ring<double> const& ring, double px, double py)
{
    bool inside = false;
    for (std::size_t i = 0, j = ring.size() - 1; i < ring.size(); j = i++)
    {
        double const yi = ring[i].y;
        double const yj = ring[j].y;
        if ((yi > py) != (yj > py))
        {
            double const x = ring[i].x + (py - yi) / (yj - yi) * (ring[j].x - ring[i].x);
            if (px < x)
                inside = !inside;
        }
    }
    return inside;
}

// Traces the outline of the pixels owned by `id` inside `bounds`.
geometry::multi_polygon<double> trace_region(std::uint32_t const* ids,
                                             std::size_t width,
                                             std::size_t height,
                                             std::uint32_t id,
                                             std::int32_t x0,
                                             std::int32_t y0,
                                             std::int32_t x1,
                                             std::int32_t y1,
                                             double tolerance)
{
    auto owns = [&](std::int32_t x, std::int32_t y) {
        if (x < 0 || y < 0 || x >= static_cast<std::int32_t>(width) || y >= static_cast<std::int32_t>(height))
            return false;
        return ids[static_cast<std::size_t>(y) * width + static_cast<std::size_t>(x)] == id;
    };

    std::vector<boundary_edge> edges;
    for (std::int32_t y = y0; y < y1; ++y)
    {
        for (std::int32_t x = x0; x < x1; ++x)
        {
            if (!owns(x, y))
                continue;
            if (!owns(x, y - 1))
                edges.push_back({{x, y}, {x + 1, y}, false});
            if (!owns(x + 1, y))
                edges.push_back({{x + 1, y}, {x + 1, y + 1}, false});
            if (!owns(x, y + 1))
                edges.push_back({{x + 1, y + 1}, {x, y + 1}, false});
            if (!owns(x - 1, y))
                edges.push_back({{x, y + 1}, {x, y}, false});
        }
    }

    std::unordered_map<corner, std::vector<std::size_t>, corner_hash> outgoing;
    for (std::size_t i = 0; i < edges.size(); ++i)
    {
        outgoing[edges[i].from].push_back(i);
    }

    // ponytail: at a vertex where two components touch diagonally there are two
    // outgoing edges and the first unused one is taken. The traced rings still
    // cover exactly the same pixels; only the split between components is
    // arbitrary. Pick by turn direction if that distinction ever matters.
    std::vector<geometry::linear_ring<double>> rings;
    for (std::size_t start = 0; start < edges.size(); ++start)
    {
        if (edges[start].used)
            continue;
        geometry::linear_ring<double> ring;
        std::size_t current = start;
        while (!edges[current].used)
        {
            edges[current].used = true;
            ring.emplace_back(edges[current].from.x, edges[current].from.y);
            corner const target = edges[current].to;
            auto const found = outgoing.find(target);
            if (found == outgoing.end())
                break;
            std::size_t next = edges.size();
            for (std::size_t candidate : found->second)
            {
                if (!edges[candidate].used)
                {
                    next = candidate;
                    break;
                }
            }
            if (next == edges.size())
                break;
            current = next;
        }
        if (ring.size() < 3)
            continue;
        ring.emplace_back(ring.front().x, ring.front().y);
        rings.push_back(std::move(ring));
    }

    // Exteriors first, then place each hole in the smallest exterior that holds it.
    geometry::multi_polygon<double> result;
    std::vector<std::size_t> hole_indices;
    std::vector<double> areas(rings.size(), 0.0);
    std::vector<std::size_t> exterior_of(rings.size(), 0);
    for (std::size_t i = 0; i < rings.size(); ++i)
    {
        areas[i] = signed_area(rings[i]);
        if (areas[i] > 0.0)
        {
            exterior_of[i] = result.size();
            geometry::polygon<double> polygon;
            polygon.push_back(rings[i]);
            result.push_back(std::move(polygon));
        }
        else
        {
            hole_indices.push_back(i);
        }
    }
    for (std::size_t hole : hole_indices)
    {
        double best_area = 0.0;
        std::size_t best = result.size();
        for (std::size_t i = 0; i < rings.size(); ++i)
        {
            if (areas[i] <= 0.0)
                continue;
            if (!point_in_ring(rings[i], rings[hole].front().x, rings[hole].front().y))
                continue;
            if (best == result.size() || areas[i] < best_area)
            {
                best_area = areas[i];
                best = exterior_of[i];
            }
        }
        if (best != result.size())
            result[best].push_back(rings[hole]);
    }

    // Simplify last, so exterior/hole classification used the exact outlines.
    geometry::multi_polygon<double> simplified;
    for (auto const& polygon : result)
    {
        geometry::polygon<double> out;
        for (auto const& ring : polygon)
        {
            auto reduced = simplify_ring(drop_collinear(ring), tolerance);
            if (reduced.size() >= 4)
                out.push_back(std::move(reduced));
        }
        if (!out.empty())
            simplified.push_back(std::move(out));
    }
    return simplified;
}

} // namespace

char const* to_string(display_element_type type)
{
    switch (type)
    {
        case display_element_type::point:
            return "point";
        case display_element_type::line:
            return "line";
        case display_element_type::line_pattern:
            return "line_pattern";
        case display_element_type::polygon:
            return "polygon";
        case display_element_type::polygon_pattern:
            return "polygon_pattern";
        case display_element_type::raster:
            return "raster";
        case display_element_type::text:
            return "text";
        case display_element_type::shield:
            return "shield";
        case display_element_type::building:
            return "building";
        case display_element_type::markers:
            return "markers";
        case display_element_type::group:
            return "group";
        case display_element_type::dot:
            return "dot";
        case display_element_type::debug:
            return "debug";
    }
    return "unknown";
}

visibility_tracker::visibility_tracker(render_observer& observer, image_rgba8 const& target)
    : observer_(observer),
      width_(target.width()),
      height_(target.height())
{
    levels_.push_back(level{&target, image_gray32(width_, height_), target});
    stack_.push_back(0);
}

void visibility_tracker::push_target(image_rgba8 const& target)
{
    std::size_t const current = stack_.back();
    if (current != untracked && levels_[current].target == &target)
    {
        // Aliased push: same render target, nothing new to track.
        stack_.push_back(current);
    }
    else if (target.width() != width_ || target.height() != height_)
    {
        // Inflated buffer used by image filters - not tracked.
        stack_.push_back(untracked);
    }
    else
    {
        levels_.push_back(level{&target, image_gray32(width_, height_), target});
        stack_.push_back(levels_.size() - 1);
    }
}

void visibility_tracker::pop_target(image_rgba8 const& parent)
{
    std::size_t const popped = stack_.back();
    stack_.pop_back();
    std::size_t const current = stack_.back();
    if (current == untracked || popped == current)
        return;

    std::size_t const n = width_ * height_;
    level& dst = levels_[current];
    std::uint32_t const* previous = dst.previous.data();
    std::uint32_t const* composited = parent.data();
    std::uint32_t* ids = dst.ids.data();

    if (popped == untracked)
    {
        // Something untracked was composited in; claim the changed pixels for
        // no element rather than leaving them attributed to whatever was there.
        for (std::size_t i = 0; i < n; ++i)
        {
            if (composited[i] != previous[i])
                ids[i] = 0;
        }
    }
    else
    {
        std::uint32_t const* child_ids = levels_[popped].ids.data();
        for (std::size_t i = 0; i < n; ++i)
        {
            if (composited[i] != previous[i])
                ids[i] = child_ids[i];
        }
        levels_.pop_back();
    }
    dst.previous = parent;
}

element_id visibility_tracker::begin(feature_impl const& feature,
                                     symbolizer_base const& sym,
                                     display_element_type type,
                                     value rendered_text)
{
    if (!tracking())
        return 0;
    if (sequence_ >= max_element_id)
        throw std::runtime_error("mapnik: too many tracked render elements");
    element_id const id = ++sequence_;
    render_event const event{id, id, current_layer(), feature, sym, type, std::move(rendered_text), variables_};
    observer_.begin_element(event);
    return id;
}

void visibility_tracker::end(element_id id, image_rgba8 const& target)
{
    std::uint64_t drawn = 0;
    std::size_t const current = stack_.back();
    if (current != untracked)
    {
        level& lvl = levels_[current];
        std::size_t const n = width_ * height_;
        std::uint32_t const* rendered = target.data();
        std::uint32_t* previous = lvl.previous.data();
        std::uint32_t* ids = lvl.ids.data();
        std::uint32_t const id32 = static_cast<std::uint32_t>(id);
        for (std::size_t i = 0; i < n; ++i)
        {
            if (rendered[i] != previous[i])
            {
                previous[i] = rendered[i];
                ids[i] = id32;
                ++drawn;
            }
        }
    }
    observer_.end_element(id, drawn);
}

void visibility_tracker::finalize()
{
    struct extent
    {
        std::int32_t x0;
        std::int32_t y0;
        std::int32_t x1; // exclusive
        std::int32_t y1; // exclusive
        std::uint64_t count;
    };

    std::map<std::uint32_t, extent> bounds;
    std::uint32_t const* ids = levels_.front().ids.data();
    for (std::size_t y = 0; y < height_; ++y)
    {
        for (std::size_t x = 0; x < width_; ++x)
        {
            std::uint32_t const id = ids[y * width_ + x];
            if (id == 0)
                continue;
            auto const ix = static_cast<std::int32_t>(x);
            auto const iy = static_cast<std::int32_t>(y);
            auto const inserted = bounds.emplace(id, extent{ix, iy, ix + 1, iy + 1, 1});
            if (!inserted.second)
            {
                extent& e = inserted.first->second;
                e.x0 = std::min(e.x0, ix);
                e.y0 = std::min(e.y0, iy);
                e.x1 = std::max(e.x1, ix + 1);
                e.y1 = std::max(e.y1, iy + 1);
                ++e.count;
            }
        }
    }

    double const tolerance = observer_.geometry_simplify_tolerance();
    std::vector<element_visibility> visible;
    visible.reserve(bounds.size());
    for (auto const& entry : bounds)
    {
        extent const& e = entry.second;
        element_visibility item;
        item.id = entry.first;
        item.pixel_bbox = box2d<double>(e.x0, e.y0, e.x1, e.y1);
        item.pixel_count = e.count;
        item.pixel_geometry = trace_region(ids, width_, height_, entry.first, e.x0, e.y0, e.x1, e.y1, tolerance);
        visible.push_back(std::move(item));
    }
    observer_.finalize(visible);
}

} // namespace mapnik
