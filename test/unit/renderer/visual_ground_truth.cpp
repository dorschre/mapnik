#include "catch.hpp"

#include <mapnik/agg_renderer.hpp>
#include <mapnik/expression.hpp>
#include <mapnik/feature.hpp>
#include <mapnik/feature_factory.hpp>
#include <mapnik/feature_type_style.hpp>
#include <mapnik/image.hpp>
#include <mapnik/image_util.hpp>
#include <mapnik/layer.hpp>
#include <mapnik/map.hpp>
#include <mapnik/memory_datasource.hpp>
#include <mapnik/params.hpp>
#include <mapnik/rule.hpp>
#include <mapnik/symbolizer.hpp>
#include <mapnik/text/formatting/text.hpp>
#include <mapnik/text/placements/dummy.hpp>
#include <mapnik/unicode.hpp>
#include <mapnik/visual_ground_truth.hpp>

#include <algorithm>
#include <memory>
#include <string>

namespace {

using namespace mapnik;

constexpr unsigned map_size = 64;

// A box in map coordinates; the map extent is always (0, 0, 100, 100), so
// coordinates are effectively percentages of the image.
geometry::polygon<double> make_box(double x0, double y0, double x1, double y1)
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

std::shared_ptr<memory_datasource> make_datasource()
{
    parameters params;
    params["type"] = "memory";
    return std::make_shared<memory_datasource>(params);
}

// Adds a layer drawing exactly the given symbolizers over the given features.
// Layers render in insertion order, so a later layer draws on top.
void add_layer(Map& map,
               std::string const& name,
               std::shared_ptr<memory_datasource> const& ds,
               rule&& r)
{
    feature_type_style style;
    style.add_rule(std::move(r));
    map.insert_style(name, std::move(style));
    layer lyr(name);
    lyr.set_datasource(ds);
    lyr.add_style(name);
    map.add_layer(lyr);
}

rule polygon_rule(color const& fill)
{
    rule r;
    polygon_symbolizer sym;
    put(sym, keys::fill, fill);
    put(sym, keys::clip, false);
    r.append(std::move(sym));
    return r;
}

feature_ptr make_feature(context_ptr const& ctx, value_integer id, geometry::geometry<double> geom)
{
    feature_ptr feature(feature_factory::create(ctx, id));
    feature->set_geometry(std::move(geom));
    return feature;
}

// A layer of one filled box.
std::shared_ptr<memory_datasource> box_source(value_integer id, double x0, double y0, double x1, double y1)
{
    auto ds = make_datasource();
    context_ptr ctx = std::make_shared<context_type>();
    ds->push(make_feature(ctx, id, make_box(x0, y0, x1, y1)));
    return ds;
}

Map make_map()
{
    Map map(map_size, map_size);
    map.set_background(color("white"));
    return map;
}

void zoom(Map& map)
{
    map.zoom_to_box(box2d<double>(0, 0, 100, 100));
}

image_rgba8 render(Map& map, visual_ground_truth_collector* collector)
{
    image_rgba8 image(map.width(), map.height());
    agg_renderer<image_rgba8> ren(map, image);
    if (collector)
        ren.set_render_observer(collector);
    ren.apply();
    return image;
}

std::size_t count_type(visual_ground_truth_collector const& collector, display_element_type type)
{
    return static_cast<std::size_t>(std::count_if(collector.displayed_elements().begin(),
                                                  collector.displayed_elements().end(),
                                                  [type](displayed_element const& e) { return e.type == type; }));
}

bool has_object(visual_ground_truth_collector const& collector, std::string const& layer, value_integer id)
{
    for (auto const& object : collector.visible_objects())
    {
        if (object.layer_name == layer && object.feature_id == id)
            return true;
    }
    return false;
}

visible_object const* find_object(visual_ground_truth_collector const& collector, std::string const& layer)
{
    for (auto const& object : collector.visible_objects())
    {
        if (object.layer_name == layer)
            return &object;
    }
    return nullptr;
}

// A map with a text label over a point, optionally with extra attributes.
text_symbolizer make_text_symbolizer(std::string const& expression)
{
    text_symbolizer sym;
    text_placements_ptr placements = std::make_shared<text_placements_dummy>();
    placements->defaults.format_defaults.face_name = "DejaVu Sans Book";
    placements->defaults.format_defaults.text_size = 12.0;
    placements->defaults.format_defaults.fill = color(0, 0, 0);
    placements->defaults.set_format_tree(
      std::make_shared<formatting::text_node>(parse_expression(expression)));
    put<text_placements_ptr>(sym, keys::text_placements_, placements);
    return sym;
}

char const* font_dir = "fonts/dejavu-fonts-ttf-2.37/ttf";

// Fonts are registered on the Map, never on the global freetype_engine: a
// global registration would change the faces other test cases shape with.
bool fonts_available()
{
    Map probe(1, 1);
    return probe.register_fonts(font_dir, true);
}

} // namespace

TEST_CASE("visual ground truth")
{
    SECTION("no observer leaves rendering unchanged")
    {
        auto build = []() {
            Map map = make_map();
            add_layer(map, "bottom", box_source(1, 10, 10, 60, 60), polygon_rule(color("red")));
            add_layer(map, "top", box_source(2, 40, 40, 90, 90), polygon_rule(color("blue")));
            zoom(map);
            return map;
        };

        Map plain = build();
        image_rgba8 const without = render(plain, nullptr);

        Map tracked = build();
        visual_ground_truth_collector collector;
        image_rgba8 const with = render(tracked, &collector);

        REQUIRE(without.width() == with.width());
        REQUIRE(without.height() == with.height());
        for (std::size_t y = 0; y < without.height(); ++y)
        {
            for (std::size_t x = 0; x < without.width(); ++x)
            {
                REQUIRE(without(x, y) == with(x, y));
            }
        }
        REQUIRE(collector.displayed_elements().size() == 2);
    }

    SECTION("complete occlusion removes the covered element")
    {
        Map map = make_map();
        add_layer(map, "bottom", box_source(1, 20, 20, 60, 60), polygon_rule(color("red")));
        add_layer(map, "top", box_source(2, 10, 10, 90, 90), polygon_rule(color("blue")));
        zoom(map);

        visual_ground_truth_collector collector;
        render(map, &collector);

        REQUIRE(collector.displayed_elements().size() == 1);
        CHECK(collector.displayed_elements().front().layer_name == "top");
        CHECK(collector.visible_objects().size() == 1);
        CHECK_FALSE(has_object(collector, "bottom", 1));
    }

    SECTION("partial occlusion preserves both elements")
    {
        Map map = make_map();
        add_layer(map, "bottom", box_source(1, 10, 10, 60, 60), polygon_rule(color("red")));
        add_layer(map, "top", box_source(2, 40, 40, 90, 90), polygon_rule(color("blue")));
        zoom(map);

        visual_ground_truth_collector collector;
        render(map, &collector);

        REQUIRE(collector.displayed_elements().size() == 2);
        CHECK(has_object(collector, "bottom", 1));
        CHECK(has_object(collector, "top", 2));
    }

    SECTION("off-screen geometry produces no element")
    {
        Map map = make_map();
        add_layer(map, "onscreen", box_source(1, 10, 10, 40, 40), polygon_rule(color("red")));
        add_layer(map, "offscreen", box_source(2, 500, 500, 600, 600), polygon_rule(color("blue")));
        zoom(map);

        visual_ground_truth_collector collector;
        render(map, &collector);

        REQUIRE(collector.displayed_elements().size() == 1);
        CHECK(collector.displayed_elements().front().layer_name == "onscreen");
        CHECK_FALSE(has_object(collector, "offscreen", 2));
    }

    SECTION("sequence follows render order even when earlier elements disappear")
    {
        Map map = make_map();
        add_layer(map, "a", box_source(1, 10, 10, 40, 40), polygon_rule(color("red")));
        add_layer(map, "b", box_source(2, 10, 10, 40, 40), polygon_rule(color("green")));
        add_layer(map, "c", box_source(3, 60, 60, 90, 90), polygon_rule(color("blue")));
        zoom(map);

        visual_ground_truth_collector collector;
        render(map, &collector);

        // "a" is fully covered by "b"; the remaining sequences keep their
        // original render order and skip the vanished one.
        REQUIRE(collector.displayed_elements().size() == 2);
        auto const& elements = collector.displayed_elements();
        CHECK(elements[0].layer_name == "b");
        CHECK(elements[1].layer_name == "c");
        CHECK(elements[0].sequence == 2);
        CHECK(elements[1].sequence == 3);
    }

    SECTION("layer identity distinguishes equal feature ids")
    {
        Map map = make_map();
        add_layer(map, "left", box_source(1, 5, 5, 45, 45), polygon_rule(color("red")));
        add_layer(map, "right", box_source(1, 55, 55, 95, 95), polygon_rule(color("blue")));
        zoom(map);

        visual_ground_truth_collector collector;
        render(map, &collector);

        REQUIRE(collector.visible_objects().size() == 2);
        CHECK(has_object(collector, "left", 1));
        CHECK(has_object(collector, "right", 1));
    }

    SECTION("multiple symbolizers aggregate into one visible object")
    {
        Map map = make_map();
        auto ds = make_datasource();
        context_ptr ctx = std::make_shared<context_type>();
        ds->push(make_feature(ctx, 42, make_box(10, 10, 90, 90)));

        rule r;
        {
            polygon_symbolizer poly;
            put(poly, keys::fill, color("red"));
            put(poly, keys::clip, false);
            r.append(std::move(poly));
        }
        {
            line_symbolizer line;
            put(line, keys::stroke, color("black"));
            put(line, keys::stroke_width, 3.0);
            put(line, keys::clip, false);
            r.append(std::move(line));
        }
        add_layer(map, "roads", ds, std::move(r));
        zoom(map);

        visual_ground_truth_collector collector;
        render(map, &collector);

        CHECK(count_type(collector, display_element_type::polygon) == 1);
        CHECK(count_type(collector, display_element_type::line) == 1);
        REQUIRE(collector.visible_objects().size() == 1);
        CHECK(collector.visible_objects().front().displayed_element_ids.size() == 2);
    }

    SECTION("a fully hidden object is absent")
    {
        Map map = make_map();
        add_layer(map, "hidden", box_source(1, 20, 20, 40, 40), polygon_rule(color("red")));
        add_layer(map, "cover", box_source(2, 0, 0, 100, 100), polygon_rule(color("blue")));
        zoom(map);

        visual_ground_truth_collector collector;
        render(map, &collector);

        CHECK_FALSE(has_object(collector, "hidden", 1));
        REQUIRE(collector.visible_objects().size() == 1);
    }

    SECTION("cross-type occlusion: polygon over line")
    {
        Map map = make_map();
        auto ds = make_datasource();
        context_ptr ctx = std::make_shared<context_type>();
        geometry::line_string<double> path;
        path.emplace_back(20, 50);
        path.emplace_back(45, 50);
        ds->push(make_feature(ctx, 1, std::move(path)));

        rule line_r;
        line_symbolizer line;
        put(line, keys::stroke, color("black"));
        put(line, keys::stroke_width, 4.0);
        put(line, keys::clip, false);
        line_r.append(std::move(line));
        add_layer(map, "line", ds, std::move(line_r));
        add_layer(map, "cover", box_source(2, 0, 0, 100, 100), polygon_rule(color("blue")));
        zoom(map);

        visual_ground_truth_collector collector;
        render(map, &collector);

        CHECK(count_type(collector, display_element_type::line) == 0);
        CHECK(count_type(collector, display_element_type::polygon) == 1);
    }

    SECTION("visible pixel bbox reflects surviving pixels")
    {
        Map map = make_map();
        // Left half red, then the right three quarters covered in blue.
        add_layer(map, "bottom", box_source(1, 0, 0, 50, 100), polygon_rule(color("red")));
        add_layer(map, "top", box_source(2, 25, 0, 100, 100), polygon_rule(color("blue")));
        zoom(map);

        visual_ground_truth_collector collector;
        render(map, &collector);

        auto const* bottom = find_object(collector, "bottom");
        REQUIRE(bottom != nullptr);
        REQUIRE(bottom->visible_pixel_bbox);
        // Only the leftmost quarter of the image is still red.
        CHECK(bottom->visible_pixel_bbox->minx() == Approx(0.0));
        CHECK(bottom->visible_pixel_bbox->maxx() < 0.5 * map_size + 1.0);
    }
}

TEST_CASE("visual ground truth - attributes")
{
    auto build = [](visual_ground_truth_collector& collector) {
        Map map = make_map();
        auto ds = make_datasource();
        context_ptr ctx = std::make_shared<context_type>();
        ctx->push("name");
        ctx->push("population");
        ctx->push("uri");
        feature_ptr feature = make_feature(ctx, 7, make_box(10, 10, 90, 90));
        transcoder tr("utf-8");
        feature->put("name", tr.transcode("Berlin"));
        feature->put("population", static_cast<value_integer>(3878100));
        feature->put("uri", tr.transcode("https://example.org/place/7"));
        ds->push(feature);
        add_layer(map, "places", ds, polygon_rule(color("red")));
        zoom(map);
        render(map, &collector);
    };

    SECTION("source attributes are not retained unless requested")
    {
        visual_ground_truth_collector collector;
        build(collector);
        REQUIRE(collector.visible_objects().size() == 1);
        auto const& object = collector.visible_objects().front();
        // A memory datasource hands back whole features regardless of the
        // queried property names; without an explicit request none of that is
        // retained, and nothing here is rendered as text.
        CHECK(object.visible_properties.empty());
        CHECK(object.source_properties.empty());
    }

    SECTION("full attribute collection keeps source data out of visible properties")
    {
        visual_ground_truth_collector collector;
        collector.set_collect_all_attributes(true);
        collector.set_identity_attributes({"uri"});
        build(collector);

        REQUIRE(collector.visible_objects().size() == 1);
        auto const& object = collector.visible_objects().front();

        // Nothing is rendered as text, so nothing is visually available.
        CHECK(object.visible_properties.empty());
        CHECK(object.visible_properties.count("population") == 0);

        // Identity metadata is explicitly separate and non-visual.
        CHECK(object.identity_metadata.count("uri") == 1);
        CHECK(object.identity_metadata.count("layer") == 1);
        CHECK(object.identity_metadata.count("feature_id") == 1);

        // Everything else is source-only.
        CHECK(object.source_properties.count("population") == 1);
        CHECK(object.source_properties.count("name") == 1);
        CHECK(object.source_properties.count("uri") == 0);
    }
}

TEST_CASE("visual ground truth - text")
{
    if (!fonts_available())
        return;

    auto label_map = [](bool cover) {
        Map map = make_map();
        map.register_fonts(font_dir, true);
        auto ds = make_datasource();
        context_ptr ctx = std::make_shared<context_type>();
        ctx->push("name");
        feature_ptr feature(feature_factory::create(ctx, 20));
        transcoder tr("utf-8");
        feature->put("name", tr.transcode("Berlin"));
        feature->set_geometry(geometry::point<double>(50, 50));
        ds->push(feature);

        rule r;
        r.append(make_text_symbolizer("[name]"));
        add_layer(map, "places", ds, std::move(r));
        if (cover)
        {
            add_layer(map, "cover", box_source(99, 0, 0, 100, 100), polygon_rule(color("blue")));
        }
        zoom(map);
        return map;
    };

    SECTION("a visible label becomes a visible property")
    {
        Map map = label_map(false);
        visual_ground_truth_collector collector;
        render(map, &collector);

        REQUIRE(count_type(collector, display_element_type::text) == 1);
        auto const* object = find_object(collector, "places");
        REQUIRE(object != nullptr);
        REQUIRE(object->visible_properties.count("label") == 1);
        CHECK(object->visible_properties.at("label").to_string() == "Berlin");
    }

    SECTION("a fully covered label is not a visible property")
    {
        Map map = label_map(true);
        visual_ground_truth_collector collector;
        render(map, &collector);

        CHECK(count_type(collector, display_element_type::text) == 0);
        CHECK(find_object(collector, "places") == nullptr);
    }

    SECTION("a label rejected by collision produces no element")
    {
        Map map = make_map();
        map.register_fonts(font_dir, true);
        auto ds = make_datasource();
        context_ptr ctx = std::make_shared<context_type>();
        ctx->push("name");
        transcoder tr("utf-8");
        for (value_integer id : {1, 2})
        {
            feature_ptr feature(feature_factory::create(ctx, id));
            feature->put("name", tr.transcode("Berlin"));
            feature->set_geometry(geometry::point<double>(50, 50));
            ds->push(feature);
        }

        rule r;
        r.append(make_text_symbolizer("[name]"));
        add_layer(map, "places", ds, std::move(r));
        zoom(map);

        visual_ground_truth_collector collector;
        render(map, &collector);

        // Two coincident labels, one of them rejected by the collision detector.
        CHECK(count_type(collector, display_element_type::text) == 1);
        CHECK(collector.visible_objects().size() == 1);
    }
}

TEST_CASE("visual ground truth - markers")
{
    SECTION("each marker placement is independent")
    {
        Map map = make_map();
        auto ds = make_datasource();
        context_ptr ctx = std::make_shared<context_type>();
        geometry::multi_point<double> points;
        points.emplace_back(20, 50);
        points.emplace_back(50, 50);
        points.emplace_back(80, 50);
        ds->push(make_feature(ctx, 1, std::move(points)));

        rule r;
        markers_symbolizer sym;
        put(sym, keys::fill, color("red"));
        put(sym, keys::width, 8.0);
        put(sym, keys::height, 8.0);
        put(sym, keys::allow_overlap, true);
        r.append(std::move(sym));
        add_layer(map, "dots", ds, std::move(r));
        // Cover the middle marker only.
        add_layer(map, "cover", box_source(2, 40, 30, 60, 70), polygon_rule(color("blue")));
        zoom(map);

        visual_ground_truth_collector collector;
        render(map, &collector);

        CHECK(count_type(collector, display_element_type::markers) == 2);
        REQUIRE(collector.visible_objects().size() == 2);
    }
}

TEST_CASE("visual ground truth - json")
{
    Map map = make_map();
    add_layer(map, "places", box_source(20, 10, 10, 90, 90), polygon_rule(color("red")));
    zoom(map);

    visual_ground_truth_collector collector;
    render(map, &collector);

    std::string const json = collector.to_json();
    CHECK(json.find("\"objects\"") != std::string::npos);
    CHECK(json.find("\"identity\"") != std::string::npos);
    CHECK(json.find("\"visible_properties\"") != std::string::npos);
    CHECK(json.find("\"displayed_elements\"") != std::string::npos);
    CHECK(json.find("places") != std::string::npos);
}
