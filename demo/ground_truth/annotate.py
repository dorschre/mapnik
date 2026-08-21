#!/usr/bin/env python3
"""Draw mapnik visual ground truth on top of the map image it came from.

    python3 demo/ground_truth/annotate.py example.png example.json annotated.png

Every visible object is outlined along its *visible* geometry - the shape that
actually survived to the final image, holes included - and labelled with its
layer, feature id and, when the map renders one, its visible label.

Nothing here inspects the map data: the annotation is drawn purely from the
JSON, so whatever it shows is exactly what the ground truth claims.
"""

import argparse
import colorsys
import json
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("this tool needs Pillow: pip install Pillow")


def layer_color(name, alpha):
    """A stable, well-spread colour per layer name."""
    hue = (hash(name) % 360) / 360.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.85, 0.95)
    return (int(r * 255), int(g * 255), int(b * 255), alpha)


def ring_area(ring):
    total = 0.0
    for i in range(len(ring) - 1):
        total += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
    return abs(total) / 2.0


def load_font(size):
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "fonts/dejavu-fonts-ttf-2.37/ttf/DejaVuSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_object(overlay, outline, obj, opts, font):
    layer = obj["identity"]["layer"]
    fill = layer_color(layer, opts.fill_alpha)
    edge = layer_color(layer, 255)

    geometry = obj.get("visible_geometry")
    if geometry and not opts.bbox_only:
        for polygon in geometry["coordinates"]:
            # Overdraw and dash patterns chop an element's visible area into
            # many small parts; skipping the smallest keeps a dense map legible.
            if opts.min_part_area and ring_area(polygon[0]) < opts.min_part_area:
                continue
            # Ring 0 is the exterior, the rest are holes. Painting the exterior
            # and then punching the holes back out keeps a partly covered
            # object from being tinted where it is not actually visible.
            exterior = [tuple(p) for p in polygon[0]]
            if len(exterior) >= 3:
                overlay.polygon(exterior, fill=fill)
            for hole in polygon[1:]:
                ring = [tuple(p) for p in hole]
                if len(ring) >= 3:
                    overlay.polygon(ring, fill=(0, 0, 0, 0))
            for ring in polygon:
                pts = [tuple(p) for p in ring]
                if len(pts) >= 2:
                    outline.line(pts, fill=edge, width=opts.line_width)

    box = obj.get("visible_pixel_bbox")
    if box and (opts.bbox_only or opts.bbox):
        outline.rectangle(box, outline=edge, width=opts.line_width)

    if box and not opts.no_text:
        text = "{}#{}".format(layer, obj["identity"]["feature_id"])
        label = obj.get("visible_properties", {}).get("label")
        if label:
            text += '  "{}"'.format(label)
        x, y = box[0], box[1]
        tb = outline.textbbox((x, y), text, font=font)
        pad = 2
        outline.rectangle(
            (tb[0] - pad, tb[1] - pad, tb[2] + pad, tb[3] + pad), fill=(255, 255, 255, 235)
        )
        outline.text((x, y), text, fill=edge[:3] + (255,), font=font)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="map image the ground truth was derived from")
    parser.add_argument("ground_truth", help="ground truth JSON")
    parser.add_argument("output", nargs="?", default=None, help="annotated PNG (default: <image>.annotated.png)")
    parser.add_argument("--bbox", action="store_true", help="also draw bounding boxes")
    parser.add_argument("--bbox-only", action="store_true", help="draw bounding boxes instead of geometry")
    parser.add_argument("--fill-alpha", type=int, default=70, help="0-255, 0 for outlines only")
    parser.add_argument("--line-width", type=int, default=2)
    parser.add_argument("--font-size", type=int, default=13)
    parser.add_argument("--layer", action="append", help="only annotate this layer (repeatable)")
    parser.add_argument("--no-text", action="store_true", help="outlines only, no per-object caption")
    parser.add_argument("--min-part-area", type=float, default=0.0,
                        help="skip geometry parts smaller than this many square pixels")
    parser.add_argument("--min-pixels", type=int, default=0,
                        help="skip objects with fewer visible pixels (useful on dense maps)")
    parser.add_argument("--list", type=int, default=15, help="how many objects to list, largest first")
    parser.add_argument("--labelled-only", action="store_true",
                        help="only objects that carry a visible label")
    opts = parser.parse_args()

    with open(opts.ground_truth) as handle:
        truth = json.load(handle)
    objects = truth["objects"]
    if opts.layer:
        objects = [o for o in objects if o["identity"]["layer"] in opts.layer]
    if opts.min_pixels:
        objects = [o for o in objects if o.get("visible_pixel_count", 0) >= opts.min_pixels]
    if opts.labelled_only:
        objects = [o for o in objects if "label" in o.get("visible_properties", {})]

    base = Image.open(opts.image).convert("RGBA")
    fills = Image.new("RGBA", base.size, (0, 0, 0, 0))
    lines = Image.new("RGBA", base.size, (0, 0, 0, 0))
    font = load_font(opts.font_size)

    for obj in objects:
        draw_object(ImageDraw.Draw(fills, "RGBA"), ImageDraw.Draw(lines, "RGBA"), obj, opts, font)

    out_path = opts.output or opts.image.rsplit(".", 1)[0] + ".annotated.png"
    Image.alpha_composite(Image.alpha_composite(base, fills), lines).save(out_path)

    print("wrote {} - {} visible object(s)".format(out_path, len(objects)))
    shown = sorted(objects, key=lambda o: -o.get("visible_pixel_count", 0))[: opts.list]
    for obj in shown:
        label = obj.get("visible_properties", {}).get("label", "-")
        print(
            "  {:9} #{:<6} {:>8} px  label={}".format(
                obj["identity"]["layer"],
                obj["identity"]["feature_id"],
                obj.get("visible_pixel_count", 0),
                label,
            )
        )
    if len(objects) > len(shown):
        print("  ... {} more".format(len(objects) - len(shown)))


if __name__ == "__main__":
    main()
