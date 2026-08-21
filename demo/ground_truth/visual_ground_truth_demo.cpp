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

// Renders a small synthetic map and writes, from the same AGG pass, both the
// image and the machine-readable visual ground truth derived from it.
//
//   ./mapnik-ground-truth-demo [output-prefix] [font-dir]
//
// The scene is built so that every rule of the extraction is observable:
//  - "sunken_reef" is drawn and then completely covered by the lake, so it is
//    absent from the output even though it rendered.
//  - "coast_road" is drawn with a casing and a fill, so it produces two
//    displayed elements but exactly one visible object.
//  - "Old Harbour" is labelled, then painted over by the construction overlay,
//    so neither the object nor its label survives.
//  - "Ghost Island" lies outside the viewport and never appears.
//  - Every city carries population/country/uri attributes that are *not* drawn;
//    they stay out of visible_properties.

#include <mapnik/agg_renderer.hpp>
#include <mapnik/color.hpp>
#include <mapnik/expression.hpp>
#include <mapnik/feature_factory.hpp>
#include <mapnik/feature_type_style.hpp>
#include <mapnik/image_util.hpp>
#include <mapnik/layer.hpp>
#include <mapnik/map.hpp>
#include <mapnik/mapnik.hpp>
#include <mapnik/memory_datasource.hpp>
#include <mapnik/params.hpp>
#include <mapnik/rule.hpp>
#include <mapnik/symbolizer.hpp>
#include <mapnik/text/formatting/text.hpp>
#include <mapnik/text/placements/dummy.hpp>
#include <mapnik/unicode.hpp>
#include <mapnik/visual_ground_truth.hpp>

#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <string>

using namespace mapnik;

namespace {

parameters memory_params()
{
    parameters params;
    params["type"] = "memory";
    return params;
}

geometry::polygon<double> box(double x0, double y0, double x1, double y1)
{
    geometry::polygon<double> poly;
    geometry::linear_ring<double> ring;
    ring.emplace_back(x0, y0);
    ring.emplace_back(x1, y0);
    ring.emplace_back(x1, y1);
    ring.emplace_back(x0, y1);
    ring.emplace_back(x0, y0);
    poly.push_back(std::move(ring));
    return poly;
}

void add_layer(Map& map, std::string const& name, datasource_ptr ds, rule&& r)
{
    feature_type_style style;
    style.add_rule(std::move(r));
    map.insert_style(name, std::move(style));
    layer lyr(name);
    lyr.set_datasource(ds);
    lyr.add_style(name);
    map.add_layer(lyr);
}

rule filled(color const& fill)
{
    rule r;
    polygon_symbolizer sym;
    put(sym, keys::fill, fill);
    put(sym, keys::clip, false);
    r.append(std::move(sym));
    return r;
}

// A road drawn as a wide casing plus a narrow fill: two graphical occurrences
// of one source feature.
rule casing_and_fill()
{
    rule r;
    line_symbolizer casing;
    put(casing, keys::stroke, color(60, 60, 60));
    put(casing, keys::stroke_width, 9.0);
    put(casing, keys::clip, false);
    r.append(std::move(casing));

    line_symbolizer fill;
    put(fill, keys::stroke, color(255, 240, 170));
    put(fill, keys::stroke_width, 5.0);
    put(fill, keys::clip, false);
    r.append(std::move(fill));
    return r;
}

rule city_rule()
{
    rule r;
    markers_symbolizer dot;
    put(dot, keys::fill, color(200, 30, 30));
    put(dot, keys::stroke, color("white"));
    put(dot, keys::stroke_width, 1.5);
    put(dot, keys::width, 11.0);
    put(dot, keys::height, 11.0);
    put(dot, keys::allow_overlap, true);
    r.append(std::move(dot));

    text_symbolizer label;
    text_placements_ptr placements = std::make_shared<text_placements_dummy>();
    placements->defaults.format_defaults.face_name = "DejaVu Sans Book";
    placements->defaults.format_defaults.text_size = 15.0;
    placements->defaults.format_defaults.fill = color(20, 20, 20);
    placements->defaults.format_defaults.halo_fill = color("white");
    placements->defaults.format_defaults.halo_radius = 1.5;
    placements->defaults.layout_defaults.dy = -13.0;
    placements->defaults.set_format_tree(std::make_shared<formatting::text_node>(parse_expression("[name]")));
    put<text_placements_ptr>(label, keys::text_placements_, placements);
    r.append(std::move(label));
    return r;
}

feature_ptr city(context_ptr const& ctx,
                 value_integer id,
                 char const* name,
                 value_integer population,
                 char const* country,
                 double x,
                 double y)
{
    transcoder tr("utf-8");
    feature_ptr f(feature_factory::create(ctx, id));
    f->put("name", tr.transcode(name));
    f->put("population", population);
    f->put("country", tr.transcode(country));
    f->put("uri", tr.transcode(("https://example.org/place/" + std::to_string(id)).c_str()));
    f->set_geometry(geometry::point<double>(x, y));
    return f;
}

feature_ptr shape(context_ptr const& ctx, value_integer id, char const* name, geometry::geometry<double> geom)
{
    transcoder tr("utf-8");
    feature_ptr f(feature_factory::create(ctx, id));
    f->put("name", tr.transcode(name));
    f->set_geometry(std::move(geom));
    return f;
}

} // namespace

int main(int argc, char** argv)
{
    setup();

    std::string const prefix = (argc > 1) ? argv[1] : "ground_truth_demo";
    std::string const font_dir = (argc > 2) ? argv[2] : "fonts/dejavu-fonts-ttf-2.37/ttf";

    Map map(800, 600);
    map.set_background(color(214, 234, 245)); // sea
    if (!map.register_fonts(font_dir, true))
    {
        std::cerr << "warning: no fonts found in '" << font_dir << "', labels will be missing\n";
    }

    context_ptr shapes_ctx = std::make_shared<context_type>();
    shapes_ctx->push("name");

    // 1. Land.
    {
        auto ds = std::make_shared<memory_datasource>(memory_params());
        ds->push(shape(shapes_ctx, 1, "Mainland", box(4, 6, 96, 69)));
        add_layer(map, "land", ds, filled(color(238, 232, 214)));
    }

    // 2. A reef that will be completely covered by the lake below.
    {
        auto ds = std::make_shared<memory_datasource>(memory_params());
        ds->push(shape(shapes_ctx, 2, "Sunken Reef", box(60, 44, 72, 54)));
        add_layer(map, "reefs", ds, filled(color(120, 180, 120)));
    }

    // 3. The lake: covers the reef entirely, the land only partly.
    {
        auto ds = std::make_shared<memory_datasource>(memory_params());
        ds->push(shape(shapes_ctx, 3, "Great Lake", box(56, 40, 88, 60)));
        add_layer(map, "water", ds, filled(color(120, 175, 220)));
    }

    // 4. A feature entirely outside the viewport.
    {
        auto ds = std::make_shared<memory_datasource>(memory_params());
        ds->push(shape(shapes_ctx, 4, "Ghost Island", box(400, 400, 460, 460)));
        add_layer(map, "far_away", ds, filled(color(200, 60, 60)));
    }

    // 5. One road feature drawn as casing + fill.
    {
        auto ds = std::make_shared<memory_datasource>(memory_params());
        geometry::line_string<double> path;
        path.emplace_back(10, 20);
        path.emplace_back(34, 30);
        path.emplace_back(52, 18);
        path.emplace_back(84, 26);
        ds->push(shape(shapes_ctx, 10, "Coast Road", std::move(path)));
        add_layer(map, "roads", ds, casing_and_fill());
    }

    // 6. Cities: marker + label each.
    {
        auto ds = std::make_shared<memory_datasource>(memory_params());
        context_ptr ctx = std::make_shared<context_type>();
        ctx->push("name");
        ctx->push("population");
        ctx->push("country");
        ctx->push("uri");
        ds->push(city(ctx, 20, "Northport", 412000, "Arcadia", 22, 55));
        ds->push(city(ctx, 21, "Riverside", 128500, "Arcadia", 44, 32));
        ds->push(city(ctx, 22, "Old Harbour", 68000, "Arcadia", 18, 12));
        add_layer(map, "cities", ds, city_rule());
    }

    // 7. Construction overlay: buries Old Harbour and its label.
    {
        auto ds = std::make_shared<memory_datasource>(memory_params());
        ds->push(shape(shapes_ctx, 30, "Construction Site", box(8, 6, 30, 18)));
        add_layer(map, "overlay", ds, filled(color(90, 90, 95)));
    }

    map.zoom_to_box(box2d<double>(0, 0, 100, 75));

    image_rgba8 image(map.width(), map.height());
    visual_ground_truth_collector collector;
    collector.set_collect_all_attributes(true);
    collector.set_identity_attributes({"uri"});

    agg_renderer<image_rgba8> renderer(map, image);
    renderer.set_render_observer(&collector);
    renderer.apply();

    std::string const png_path = prefix + ".png";
    std::string const json_path = prefix + ".json";
    save_to_file(image, png_path, "png");
    {
        std::ofstream json(json_path.c_str());
        collector.to_json(json);
    }

    std::cout << "wrote " << png_path << " and " << json_path << "\n\n";
    std::cout << "displayed elements (graphical truth), " << collector.displayed_elements().size() << " total:\n";
    for (auto const& element : collector.displayed_elements())
    {
        std::cout << "  #" << std::setw(2) << element.id << "  " << std::setw(8) << to_string(element.type) << "  "
                  << std::setw(9) << element.layer_name << "  feature " << element.feature_id;
        if (!element.rendered_text.is_null())
            std::cout << "  text=\"" << element.rendered_text.to_string() << "\"";
        std::cout << "\n";
    }

    std::cout << "\nvisible objects (semantic truth), " << collector.visible_objects().size() << " total:\n";
    for (auto const& object : collector.visible_objects())
    {
        std::cout << "  " << std::setw(9) << object.layer_name << " #" << object.feature_id << "  elements=[";
        for (std::size_t i = 0; i < object.displayed_element_ids.size(); ++i)
        {
            std::cout << (i ? "," : "") << object.displayed_element_ids[i];
        }
        std::cout << "]";
        auto const label = object.visible_properties.find("label");
        std::cout << "  visible_label=" << (label == object.visible_properties.end() ? "-" : label->second.to_string());
        std::cout << "\n";
    }
    return 0;
}
