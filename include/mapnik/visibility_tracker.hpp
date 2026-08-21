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

#ifndef MAPNIK_VISIBILITY_TRACKER_HPP
#define MAPNIK_VISIBILITY_TRACKER_HPP

// mapnik
#include <mapnik/config.hpp>
#include <mapnik/image.hpp>
#include <mapnik/pixel_types.hpp>
#include <mapnik/render_observer.hpp>
#include <mapnik/util/noncopyable.hpp>

// stl
#include <cstddef>
#include <vector>

namespace mapnik {

/* Determines which rendering occurrences still contribute to the final image.
 *
 * Model: alongside the RGBA target the tracker keeps a previous-frame copy and
 * a parallel uint32 element-id buffer. When an element finishes rendering,
 * every pixel that differs from the previous frame is stamped with that
 * element's id and the previous frame is updated. A later element covering
 * those pixels overwrites the ids, so after the pass an element is displayed
 * exactly when its id still occurs in the id buffer.
 *
 * Memory: 8 bytes per pixel (4 id + 4 previous frame) for each *distinct*
 * render target on the buffer stack; aliased pushes cost nothing. Typical
 * depth is 1, or 2-3 for styles/layers using comp-op or opacity.
 *
 * Cost: one O(width * height) scan per element. No per-pixel allocation.
 * ponytail: whole-buffer scan; restrict to a per-element bbox hint if
 * element counts make this the bottleneck.
 *
 * Known approximations, all documented in the acceptance notes:
 *  - An element redrawing the exact same colour produces no difference and is
 *    therefore not reported as displayed.
 *  - Compositing is last-writer-wins, so an opaque element entirely covered by
 *    a semi-transparent one loses its pixels. Exact contributor tracking for
 *    transparency is deferred.
 *  - Targets whose size differs from the map (inflating image filters) are not
 *    tracked; elements drawn into them produce no displayed elements.
 */
class MAPNIK_DECL visibility_tracker : private util::noncopyable
{
  public:
    visibility_tracker(render_observer& observer, image_rgba8 const& target);

    // Mirrors agg_renderer's buffer stack. push_target must be called for every
    // push, including aliased pushes of the same buffer; pop_target must be
    // called after any compositing into the parent has happened.
    // Render-time variables, needed by observers that inspect symbolizers.
    void set_variables(attributes const* vars) { variables_ = vars; }

    void push_target(image_rgba8 const& target);
    void pop_target(image_rgba8 const& parent);

    // Layers nest, so the current layer is a stack.
    void push_layer(layer const* lay) { layers_.push_back(lay); }
    void pop_layer() { layers_.pop_back(); }
    layer const* current_layer() const { return layers_.empty() ? nullptr : layers_.back(); }

    bool tracking() const { return stack_.back() != untracked; }

    // Returns 0 when the current target is not tracked, in which case no
    // observer callbacks are made for this occurrence.
    element_id begin(feature_impl const& feature,
                     symbolizer_base const& sym,
                     display_element_type type,
                     value rendered_text = value());
    void end(element_id id, image_rgba8 const& target);

    // Resolves final visibility and hands the surviving elements to the
    // observer. Must be called once, after the last element and before any
    // post-processing of the image (e.g. demultiply).
    void finalize();

  private:
    static constexpr std::size_t untracked = static_cast<std::size_t>(-1);

    struct level
    {
        void const* target;
        image_gray32 ids;
        image_rgba8 previous;
    };

    render_observer& observer_;
    std::size_t const width_;
    std::size_t const height_;
    std::vector<level> levels_;
    std::vector<std::size_t> stack_;
    std::vector<layer const*> layers_;
    attributes const* variables_ = nullptr;
    element_id sequence_ = 0;
};

// RAII wrapper around one rendering occurrence. Inert when the tracker is null.
class element_scope : private util::noncopyable
{
  public:
    element_scope()
        : tracker_(nullptr),
          target_(nullptr),
          id_(0)
    {}

    element_scope(visibility_tracker* tracker,
                  image_rgba8 const& target,
                  feature_impl const& feature,
                  symbolizer_base const& sym,
                  display_element_type type,
                  value rendered_text = value())
        : tracker_(tracker),
          target_(&target),
          id_(tracker ? tracker->begin(feature, sym, type, std::move(rendered_text)) : 0)
    {}

    element_scope(element_scope&& rhs) noexcept
        : tracker_(rhs.tracker_),
          target_(rhs.target_),
          id_(rhs.id_)
    {
        rhs.tracker_ = nullptr;
        rhs.id_ = 0;
    }

    ~element_scope()
    {
        if (tracker_ && id_)
            tracker_->end(id_, *target_);
    }

  private:
    visibility_tracker* tracker_;
    image_rgba8 const* target_;
    element_id id_;
};

} // namespace mapnik

#endif // MAPNIK_VISIBILITY_TRACKER_HPP
