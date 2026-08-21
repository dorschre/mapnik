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

// A flat C entry point for visual ground-truth extraction, so any language with
// an FFI - Python's ctypes in particular - can render a stylesheet and get back
// both the image and the ground truth without a subprocess.
//
// No exception is allowed to cross this boundary: every entry point catches
// everything, records the message, and returns a null/failure value. Retrieve
// the message with mapnik_gt_last_error().

#include <mapnik/agg_renderer.hpp>
#include <mapnik/datasource_cache.hpp>
#include <mapnik/font_engine_freetype.hpp>
#include <mapnik/image_util.hpp>
#include <mapnik/load_map.hpp>
#include <mapnik/map.hpp>
#include <mapnik/mapnik.hpp>
#include <mapnik/proj_transform.hpp>
#include <mapnik/projection.hpp>
#include <mapnik/visual_ground_truth.hpp>

#include <cstddef>
#include <exception>
#include <set>
#include <string>

#define MAPNIK_GT_API extern "C" __attribute__((visibility("default")))

namespace {

thread_local std::string last_error;

struct gt_result
{
    std::string json;
    std::string png;
    mapnik::image_rgba8 image;
    std::size_t displayed = 0;
    std::size_t rendered = 0;
    std::size_t objects = 0;
};

} // namespace

struct mapnik_gt_result : gt_result
{};

MAPNIK_GT_API char const* mapnik_gt_last_error()
{
    return last_error.c_str();
}

// Registers datasource plugins and fonts. Either argument may be null. Safe to
// call more than once; mapnik ignores repeat registrations.
MAPNIK_GT_API int mapnik_gt_setup(char const* plugin_dir, char const* font_dir)
{
    try
    {
        last_error.clear();
        mapnik::setup();
        if (plugin_dir && *plugin_dir)
            mapnik::datasource_cache::instance().register_datasources(plugin_dir);
        if (font_dir && *font_dir)
        {
            if (!mapnik::freetype_engine::register_fonts(font_dir, true))
            {
                last_error = std::string("no fonts found in '") + font_dir + "'";
                return 1;
            }
        }
        return 0;
    }
    catch (std::exception const& e)
    {
        last_error = e.what();
        return 1;
    }
    catch (...)
    {
        last_error = "unknown error during setup";
        return 1;
    }
}

/* Renders `xml` (a file path, or the stylesheet itself when xml_is_string is
 * non-zero) and collects the ground truth from the same pass.
 *
 *   extent            null, or four doubles minx/miny/maxx/maxy; null zooms to
 *                     the full data extent
 *   extent_is_wgs84   interpret `extent` as lon/lat and reproject it
 *   base_path         resolves relative datasource paths for a string stylesheet
 *   identity_names    attribute names to report as identity metadata
 *
 * Returns null on failure; free the result with mapnik_gt_free().
 */
MAPNIK_GT_API mapnik_gt_result* mapnik_gt_render(char const* xml,
                                                 int xml_is_string,
                                                 unsigned width,
                                                 unsigned height,
                                                 double const* extent,
                                                 int extent_is_wgs84,
                                                 double scale_factor,
                                                 double simplify_tolerance,
                                                 int collect_attributes,
                                                 char const* const* identity_names,
                                                 int identity_count,
                                                 char const* base_path)
{
    try
    {
        last_error.clear();
        if (!xml)
        {
            last_error = "no stylesheet given";
            return nullptr;
        }
        if (width == 0 || height == 0)
        {
            last_error = "width and height must both be non-zero";
            return nullptr;
        }

        mapnik::Map map(width, height);
        if (xml_is_string)
            mapnik::load_map_string(map, xml, false, base_path ? base_path : "");
        else
            mapnik::load_map(map, xml);
        map.resize(width, height);

        if (extent)
        {
            mapnik::box2d<double> box(extent[0], extent[1], extent[2], extent[3]);
            if (extent_is_wgs84)
            {
                mapnik::projection const source("epsg:4326", true);
                mapnik::projection const target(map.srs(), true);
                mapnik::proj_transform const transform(source, target);
                if (!transform.forward(box))
                {
                    last_error = "could not reproject the given extent into " + map.srs();
                    return nullptr;
                }
            }
            map.zoom_to_box(box);
        }
        else
        {
            map.zoom_all();
        }

        std::set<std::string> identity;
        for (int i = 0; i < identity_count; ++i)
        {
            if (identity_names && identity_names[i])
                identity.insert(identity_names[i]);
        }

        auto result = std::make_unique<mapnik_gt_result>();
        result->image = mapnik::image_rgba8(map.width(), map.height());

        mapnik::visual_ground_truth_collector collector;
        collector.set_collect_all_attributes(collect_attributes != 0);
        collector.set_identity_attributes(identity);
        collector.set_geometry_simplify_tolerance(simplify_tolerance);

        mapnik::agg_renderer<mapnik::image_rgba8> renderer(map, result->image,
                                                           scale_factor > 0.0 ? scale_factor : 1.0);
        renderer.set_render_observer(&collector);
        renderer.apply();

        result->json = collector.to_json();
        result->png = mapnik::save_to_string(result->image, "png");
        result->displayed = collector.displayed_elements().size();
        result->rendered = collector.rendered_element_count();
        result->objects = collector.visible_objects().size();
        return result.release();
    }
    catch (std::exception const& e)
    {
        last_error = e.what();
        return nullptr;
    }
    catch (...)
    {
        last_error = "unknown error during rendering";
        return nullptr;
    }
}

MAPNIK_GT_API char const* mapnik_gt_json(mapnik_gt_result const* result)
{
    return result ? result->json.c_str() : nullptr;
}

MAPNIK_GT_API unsigned char const* mapnik_gt_png(mapnik_gt_result const* result, std::size_t* size)
{
    if (!result)
        return nullptr;
    if (size)
        *size = result->png.size();
    return reinterpret_cast<unsigned char const*>(result->png.data());
}

// Demultiplied RGBA8, row-major, width * height * 4 bytes - ready for numpy.
MAPNIK_GT_API unsigned char const* mapnik_gt_rgba(mapnik_gt_result const* result,
                                                  std::size_t* size,
                                                  unsigned* width,
                                                  unsigned* height)
{
    if (!result)
        return nullptr;
    if (size)
        *size = result->image.width() * result->image.height() * 4;
    if (width)
        *width = static_cast<unsigned>(result->image.width());
    if (height)
        *height = static_cast<unsigned>(result->image.height());
    return reinterpret_cast<unsigned char const*>(result->image.data());
}

MAPNIK_GT_API std::size_t mapnik_gt_displayed_count(mapnik_gt_result const* result)
{
    return result ? result->displayed : 0;
}

MAPNIK_GT_API std::size_t mapnik_gt_rendered_count(mapnik_gt_result const* result)
{
    return result ? result->rendered : 0;
}

MAPNIK_GT_API std::size_t mapnik_gt_object_count(mapnik_gt_result const* result)
{
    return result ? result->objects : 0;
}

MAPNIK_GT_API void mapnik_gt_free(mapnik_gt_result* result)
{
    delete result;
}
