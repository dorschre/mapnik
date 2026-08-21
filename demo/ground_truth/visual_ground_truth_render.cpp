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

// Renders any Mapnik XML stylesheet and writes, from the same AGG pass, the
// image and the visual ground truth derived from it.
//
//   mapnik-ground-truth-render <map.xml> <output-prefix> [options]
//
//     --size W H                 output size in pixels (default 1024 768)
//     --extent minx miny maxx maxy   in the map's own srs (default: zoom_all)
//     --extent-wgs84 minx miny maxx maxy
//     --scale-factor F
//     --fonts DIR                register a font directory (repeatable)
//     --plugins DIR              datasource plugin directory
//     --identity NAME            attribute to report as identity, not source
//                                metadata (repeatable, e.g. osm_id)
//     --simplify T               outline tolerance in pixels (default 0.5)
//     --no-attributes            do not collect source attributes
//     --require-identity NAME    exit non-zero unless every visible object
//                                carries this identity attribute
//     --candidates               also record occurrences that left no visible
//                                pixel, with why (occluded / outside viewport)

#include <mapnik/agg_renderer.hpp>
#include <mapnik/datasource_cache.hpp>
#include <mapnik/font_engine_freetype.hpp>
#include <mapnik/image_util.hpp>
#include <mapnik/load_map.hpp>
#include <mapnik/map.hpp>
#include <mapnik/mapnik.hpp>
#include <mapnik/projection.hpp>
#include <mapnik/proj_transform.hpp>
#include <mapnik/visual_ground_truth.hpp>

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <string>
#include <vector>

namespace {

[[noreturn]] void usage(char const* argv0, std::string const& message)
{
    std::cerr << message << "\n\nusage: " << argv0 << " <map.xml> <output-prefix> [options]\n"
              << "  --size W H | --extent minx miny maxx maxy | --extent-wgs84 minx miny maxx maxy\n"
              << "  --scale-factor F | --fonts DIR | --plugins DIR | --identity NAME\n"
              << "  --simplify T | --no-attributes | --candidates | --require-identity NAME\n";
    std::exit(2);
}

double to_double(char const* text) { return std::atof(text); }

} // namespace

int main(int argc, char** argv)
{
    using namespace mapnik;
    setup();

    if (argc < 3)
        usage(argv[0], "missing arguments");

    std::string const xml_path = argv[1];
    std::string const prefix = argv[2];

    unsigned width = 1024;
    unsigned height = 768;
    double scale_factor = 1.0;
    double simplify_tolerance = 0.5;
    bool collect_attributes = true;
    bool keep_candidates = false;
    std::string require_identity;
    bool have_extent = false;
    bool extent_is_wgs84 = false;
    box2d<double> extent;
    std::vector<std::string> font_dirs;
    std::string plugin_dir;
    std::set<std::string> identity_attributes;

    for (int i = 3; i < argc; ++i)
    {
        std::string const arg = argv[i];
        auto need = [&](int count) {
            if (i + count >= argc)
                usage(argv[0], "missing value for " + arg);
        };
        if (arg == "--size")
        {
            need(2);
            width = static_cast<unsigned>(std::atoi(argv[i + 1]));
            height = static_cast<unsigned>(std::atoi(argv[i + 2]));
            i += 2;
        }
        else if (arg == "--extent" || arg == "--extent-wgs84")
        {
            need(4);
            extent.init(to_double(argv[i + 1]), to_double(argv[i + 2]), to_double(argv[i + 3]),
                        to_double(argv[i + 4]));
            have_extent = true;
            extent_is_wgs84 = (arg == "--extent-wgs84");
            i += 4;
        }
        else if (arg == "--scale-factor")
        {
            need(1);
            scale_factor = to_double(argv[++i]);
        }
        else if (arg == "--simplify")
        {
            need(1);
            simplify_tolerance = to_double(argv[++i]);
        }
        else if (arg == "--fonts")
        {
            need(1);
            font_dirs.push_back(argv[++i]);
        }
        else if (arg == "--plugins")
        {
            need(1);
            plugin_dir = argv[++i];
        }
        else if (arg == "--identity")
        {
            need(1);
            identity_attributes.insert(argv[++i]);
        }
        else if (arg == "--no-attributes")
        {
            collect_attributes = false;
        }
        else if (arg == "--candidates")
        {
            keep_candidates = true;
        }
        else if (arg == "--require-identity")
        {
            need(1);
            require_identity = argv[++i];
            identity_attributes.insert(require_identity);
        }
        else
        {
            usage(argv[0], "unknown option: " + arg);
        }
    }

    if (!plugin_dir.empty())
        datasource_cache::instance().register_datasources(plugin_dir);

    Map map(width, height);
    // Fonts must be registered *before* load_map: a <FontSet> is resolved while
    // the stylesheet is parsed, and an unresolvable one aborts the load. A
    // plain face-name= on a symbolizer is resolved lazily and would not care.
    for (auto const& dir : font_dirs)
    {
        if (!map.register_fonts(dir, true))
            std::cerr << "warning: no fonts found in '" << dir << "'\n";
    }
    load_map(map, xml_path);
    map.resize(width, height);

    if (have_extent)
    {
        if (extent_is_wgs84)
        {
            projection const source("epsg:4326", true);
            projection const target(map.srs(), true);
            proj_transform const transform(source, target);
            if (!transform.forward(extent))
            {
                std::cerr << "error: could not reproject the given extent into " << map.srs() << "\n";
                return 1;
            }
        }
        map.zoom_to_box(extent);
    }
    else
    {
        map.zoom_all();
    }

    image_rgba8 image(map.width(), map.height());
    visual_ground_truth_collector collector;
    collector.set_collect_all_attributes(collect_attributes);
    collector.set_identity_attributes(identity_attributes);
    collector.set_geometry_simplify_tolerance(simplify_tolerance);
    collector.set_keep_candidates(keep_candidates);

    agg_renderer<image_rgba8> renderer(map, image, scale_factor);
    renderer.set_render_observer(&collector);
    renderer.apply();

    std::string const png_path = prefix + ".png";
    std::string const json_path = prefix + ".json";
    save_to_file(image, png_path, "png");
    {
        std::ofstream json(json_path.c_str());
        collector.to_json(json);
    }

    std::cout << "wrote " << png_path << " and " << json_path << "\n"
              << "  extent      " << map.get_current_extent() << " (" << map.srs() << ")\n"
              << "  elements    " << collector.displayed_elements().size() << " displayed of "
              << collector.rendered_element_count() << " rendered ("
              << (collector.rendered_element_count() - collector.displayed_elements().size())
              << " left no visible pixel)\n"
              << "  objects     " << collector.visible_objects().size() << " visible\n";

    std::size_t labelled = 0;
    for (auto const& object : collector.visible_objects())
    {
        if (object.visible_properties.count("label"))
            ++labelled;
    }
    std::cout << "  labelled    " << labelled << " objects carry a visible label\n";

    // An object without identity metadata cannot be linked back to source
    // data, which quietly limits what the ground truth is good for. Report it
    // for every requested identity attribute, whether or not it is enforced.
    for (auto const& name : identity_attributes)
    {
        std::size_t identified = 0;
        std::map<std::string, std::size_t> missing_by_layer;
        for (auto const& object : collector.visible_objects())
        {
            if (object.identity_metadata.count(name))
                ++identified;
            else
                ++missing_by_layer[object.layer_name];
        }
        std::size_t const total = collector.visible_objects().size();
        std::cout << "  identity    " << identified << " of " << total << " objects carry '" << name << "'";
        if (identified < total)
        {
            std::cout << "  -- MISSING on " << (total - identified) << ":\n";
            for (auto const& entry : missing_by_layer)
                std::cout << "                " << entry.second << "  layer '" << entry.first << "'\n";
        }
        else
        {
            std::cout << "\n";
        }
        if (!require_identity.empty() && name == require_identity && identified < total)
        {
            std::cerr << "error: --require-identity " << name << " not satisfied ("
                      << (total - identified) << " objects without it)\n";
            return 3;
        }
    }
    return 0;
}
