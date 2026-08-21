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

#ifndef MAPNIK_VISUAL_GROUND_TRUTH_HPP
#define MAPNIK_VISUAL_GROUND_TRUTH_HPP

// mapnik
#include <mapnik/config.hpp>
#include <mapnik/render_observer.hpp>

// stl
#include <cstddef>
#include <iosfwd>
#include <map>
#include <set>
#include <string>
#include <utility>
#include <vector>

namespace mapnik {

/* Owning collector producing machine-readable visual ground truth.
 *
 *   mapnik::image_rgba8 image(map.width(), map.height());
 *   mapnik::visual_ground_truth_collector collector;
 *   mapnik::agg_renderer<mapnik::image_rgba8> renderer(map, image);
 *   renderer.set_render_observer(&collector);
 *   renderer.apply();
 *
 * After apply() returns, displayed_elements() already holds final graphical
 * visibility and visible_objects() holds only source features with at least one
 * surviving displayed element. The caller never has to prune anything.
 *
 * The three attribute categories are kept strictly apart:
 *  - identity_metadata: links an object to its source; not claimed to be visible.
 *  - visible_properties: values actually manifested in the image (rendered text).
 *  - source_properties: datasource attributes that are not visually expressed.
 */
class MAPNIK_DECL visual_ground_truth_collector : public render_observer
{
  public:
    visual_ground_truth_collector() = default;

    // Attribute names to be reported as identity metadata rather than as
    // source-only properties (e.g. "uri", "osm_id", "wikidata").
    void set_identity_attributes(std::set<std::string> names) { identity_attributes_ = std::move(names); }

    // Collect every datasource attribute (as source_properties / identity
    // metadata). Requesting an attribute never makes it visually observable.
    void set_collect_all_attributes(bool collect) { collect_all_attributes_ = collect; }

    // Also retain occurrences that left no visible pixel. They are never
    // visible objects and never carry visible properties; they exist so the
    // graph can say *why* something is absent - covered, or never rasterized.
    void set_keep_candidates(bool keep) { keep_candidates_ = keep; }

    bool requires_all_attributes() const override { return collect_all_attributes_; }

    // Douglas-Peucker tolerance in pixels for traced visible outlines; 0 keeps
    // the exact pixel-corner staircase.
    void set_geometry_simplify_tolerance(double tolerance) { simplify_tolerance_ = tolerance; }
    double geometry_simplify_tolerance() const override { return simplify_tolerance_; }

    void begin_map(map_info const& info) override;
    void begin_element(render_event const& event) override;
    void end_element(element_id id, std::uint64_t drawn_pixels) override;
    void finalize(std::vector<element_visibility> const& visible) override;

    // How many rendering occurrences were tracked in total, including those
    // that turned out to be invisible. displayed_elements().size() is the
    // subset that survived.
    std::size_t rendered_element_count() const { return rendered_count_; }

    map_info const& map() const { return map_; }

    std::vector<displayed_element> const& displayed_elements() const { return displayed_; }

    // Occurrences that were drawn but left nothing visible. Empty unless
    // set_keep_candidates(true) was called.
    std::vector<displayed_element> const& invisible_elements() const { return invisible_; }
    std::vector<visible_object> const& visible_objects() const { return objects_; }

    // Machine-readable output in the shape documented in the specification.
    void to_json(std::ostream& out) const;
    std::string to_json() const;

  private:
    struct pending_element
    {
        displayed_element element;
        std::map<std::string, value> attributes;
    };

    using feature_key = std::pair<std::string, value_integer>;

    std::set<std::string> identity_attributes_;
    bool collect_all_attributes_ = false;
    bool keep_candidates_ = false;
    double simplify_tolerance_ = 0.5;
    std::size_t rendered_count_ = 0;
    map_info map_{0, 0, {}, box2d<double>(), {}, 1.0};
    std::vector<pending_element> pending_;
    std::map<feature_key, std::map<std::string, value>> attributes_;
    std::vector<displayed_element> displayed_;
    std::vector<displayed_element> invisible_;
    std::vector<visible_object> objects_;
};

} // namespace mapnik

#endif // MAPNIK_VISUAL_GROUND_TRUTH_HPP
