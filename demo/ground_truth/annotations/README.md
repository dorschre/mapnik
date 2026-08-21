# Detection / instance-segmentation labels from visual ground truth

`export_annotations.py` turns the ground truth into training labels whose
polygons are the **visible** outline of each object - the pixels that actually
survived to the image - in coordinates relative to the image.

```sh
python3 demo/ground_truth/annotations/export_annotations.py \
    demo/ground_truth/osm-carto/dataset out/ \
    --match z17 --format coco yolo normalized
```

## Where the labels come from

By default the exporter reads the ground-truth **JSON**. Pass `--source rdf` and
it derives exactly the same labels from the Turtle graph with SPARQL instead -
three queries for the canvas, the visible objects and their elements, plus a
small WKT reader for `cg:pixelGeometry`.

Both paths were compared on the same image, at both instance levels and across
all three dimensions:

| level | instances | 0D | 1D | 2D | identical |
|---|---|---|---|---|---|
| `--level object` | 878 | 460 | 234 | 184 | sets, dimensions and all 3235 polygon vertices |
| `--level element` | 1138 | 464 | 486 | 188 | sets, dimensions and all 3189 polygon vertices |

The RDF path is the slower of the two (parsing Turtle costs far more than
reading JSON, 1.5 s against 0.08 s per image), so it is worth using when the
graph is your source of truth, and the JSON path when you just want the labels.

## Output

| format | file | contents |
|---|---|---|
| `coco` | `instances.json` | COCO detection + segmentation. Pixel coordinates, as the format requires. |
| `yolo` | `<image>.txt`, `classes.txt` | one line per polygon: `class x1 y1 x2 y2 …`, all in 0..1 |
| `normalized` | `<image>.norm.json` | bbox, polygons, holes and an anchor point, all in 0..1, plus identity and any visible label |

Coordinates are relative to the image, origin top-left, y downwards - the
convention COCO and YOLO already use.

## All three dimensions are usable

```
0D poi   422   1D road  218   2D building 171
0D tree   38   1D barrier 11  2D landcover  7
```

* **0D** icons and dots get a small polygon *and* an `anchor` point, so the same
  export feeds a detector, a segmenter, or a keypoint model.
* **1D** roads, railways and waterways are exported as the drawn **ribbon**, not
  a centreline: the band a segmentation model can actually see, following the
  street through the image.
* **2D** buildings, water and landcover are polygon masks, with holes where
  something was drawn on top of them.

An object's dimension is taken from the element that owns the most visible
pixels, since a landcover patch has both a 2D fill and a 1D outline.

## Instances

`--level object` (default) gives one instance per source feature: a road broken
into fragments by junctions and labels stays **one** instance with several
parts. `--level element` gives one instance per graphical occurrence, so a
road's casing and fill become separate instances.

## Classes

`classes.json` maps stylesheet layer to training class; a layer mapped to
`null` is skipped, which is how label layers (`roads-text-name`, `addresses`)
are kept out of the annotations. Edit it to taste - it is the only place the
taxonomy lives.

## Two things to know

* **Holes are dropped in COCO segmentation**, which has no way to express them.
  They are preserved in the `.norm.json` files under `holes`. Only the
  `normalized` format is lossless.
* **These are visible-only masks.** A building half-hidden behind a label is
  annotated with the hole punched out, and a fully covered one is not annotated
  at all. That is the point - the model is trained on what is in the picture -
  but it differs from the usual practice of projecting complete source geometry,
  which teaches a model to hallucinate what it cannot see.
