# Mapnik Visual Ground-Truth Extraction — Developer Specification
**Status:** Draft v0.1  
**Target:** Mapnik AGG renderer  
**Repository:** `https://github.com/mapnik/mapnik`

**Primary goal:** Extend Mapnik's AGG rendering pipeline so that one rendering pass produces both the normal map image and a machine-readable representation of the semantic objects and graphical elements that are actually observable in that image.

**Primary use case:** Generate benchmark datasets whose gold answers contain only objects and information that are visually present in the corresponding rendered map.

## 1. Objective

This extension enables generation of **machine-readable visual ground truth** corresponding to a Mapnik-rendered map image.

For every semantic map object included in the machine-readable output, the rendered image must contain at least one visible graphical manifestation of that object.

Objects must be excluded when they:

- are present only in the datasource;
- fail style or rule filtering;
- lie completely outside the viewport;
- have all placements rejected;
- produce no rasterized output;
- or are completely occluded in the final image.

The extension must distinguish between:

```text
Datasource Feature
       |
       v
Rendered graphical occurrence
       |
       v
Final visibility resolution
       |
       v
DisplayedElement
       |
       | aggregate by source feature
       v
VisibleObject
       |
       v
Machine-readable map ground truth
```

The machine-readable representation must come from the **same AGG rendering pass** as the image. A second independent Mapnik render must not be required.

The central benchmark invariant is:

> **Gold data must not claim that an object or property is visually available when it cannot be observed in the corresponding map image.**

Existing Mapnik rendering behavior must remain unchanged when ground-truth extraction is disabled.

## 2. Core terminology
### Source feature
A feature obtained from a Mapnik datasource. A source feature may fail a style filter, lie outside the viewport, have a label rejected, produce no rasterized pixels, or be completely covered.

### Rendered element
One concrete graphical occurrence sent to AGG after relevant style evaluation, transformation, clipping, and placement logic. Examples: a point symbol, marker placement, text placement, line, polygon, or raster.

### Displayed element
A rendered element that contributes visual information to at least one pixel of the **final** rendered image.

A rendered element completely hidden by later elements must not be returned as a displayed element.

### Visible object

A **visible object** is a semantic/source map feature for which at least one associated `DisplayedElement` contributes to the final image.

`DisplayedElement` represents **graphical truth**: how something is visibly drawn.

`VisibleObject` represents **semantic truth constrained by the image**: which source objects are actually represented visually.

For example, a road feature may generate a casing, a fill, and a text label. These are separate `DisplayedElement`s but normally one `VisibleObject`.

The benchmark-oriented public output should expose `VisibleObject[]`, while `DisplayedElement[]` should remain available for tracing, debugging, geometry, and visibility analysis.

## 3. Required semantics
- A rendered, then completely covered by B → `[B]`
- A rendered, then partially covered by B → `[A, B]`
- Text rejected by collision/placement logic → no text element
- Element producing no pixels inside the viewport → no element
- Successfully placed element with at least one remaining visible pixel → displayed

For opaque rendering, an element is displayed if at least one of its rasterized pixels remains represented in final output.

## 4. Element granularity
A displayed element represents one **concrete graphical occurrence**, not merely one datasource feature.

One road feature styled with two `LineSymbolizer`s and one `TextSymbolizer` can produce:

```text
Feature #42
├── road casing
├── road fill
└── "Main Street" label
```

These have independent visibility. Repeated `MarkersSymbolizer` placements are also separate elements.

## 5. Element types
Displayed elements may be point-, line-, area-, text-, marker-, or raster-like. A starting enumeration is:

```cpp
enum class display_element_type {
    point, line, line_pattern, polygon, polygon_pattern,
    raster, text, shield, building, marker, group, dot, debug
};
```

The final enumeration should follow symbolizers supported by the current AGG renderer.

## 6. Architecture
Tracking must occur in the same AGG render:

```text
                    agg_renderer
                         |
                  rendering pass
                         |
             +-----------+-----------+
             |                       |
             v                       v
        RGBA output           element tracking
                                     |
                                     v
                              visibility state
                                     |
                                     v
                             DisplayedElement[]
```

No second independent Mapnik render may be used.

## 7. Render observer
Introduce an optional observer integration point in `agg_renderer`.

Conceptually:

```cpp
class render_observer {
public:
    virtual ~render_observer() = default;
    virtual element_id begin_element(render_event const& event) = 0;
    virtual void end_element(element_id id) = 0;
};
```

This exact signature is **not mandatory**. Passing an element ID through existing AGG contexts is acceptable if cleaner. The specification constrains behavior, not the precise internal callback API.

With no observer installed, rendering must behave as today.

## 8. API compatibility
Preferred conceptual usage:

```cpp
mapnik::image_rgba8 image(width, height);
displayed_element_collector collector;

mapnik::agg_renderer<mapnik::image_rgba8> renderer(map, image);
renderer.set_render_observer(&collector);
renderer.apply();

auto const& elements = collector.displayed_elements();
```

Existing constructors and normal `renderer.apply()` usage should remain valid.

## 9. Internal identity and render events
Every concrete rendering occurrence that may contribute to output receives a unique internal element ID. A separate monotonically increasing `sequence` preserves render order.

Conceptual event:

```cpp
struct render_event {
    std::uint64_t sequence;
    layer const& layer;
    feature_impl const& feature;
    symbolizer_base const& symbolizer;
    display_element_type type;
    std::optional<box2d<double>> pixel_bbox;
};
```

Layer identity is required because feature IDs may repeat across layers. Feature/symbolizer references are callback-scoped; consumers must copy retained data.

## 10. Visibility tracking
The renderer must maintain enough state alongside RGBA rendering to determine which elements remain visible.

For normal opaque rendering, the conceptual model is a parallel element-ID buffer:

```text
RGBA buffer            Element-ID buffer
red red blue blue       1 1 2 2
red red blue blue       1 1 2 2
```

If element 2 later completely covers element 1, the final ID buffer contains only `2`; element 1 is not displayed.

The exact representation is an implementation choice and need not literally be a second Mapnik image.

## 11. Transparency and compositing
A last-writer-wins ID buffer is insufficient for arbitrary transparency. If opaque A is covered by 50%-transparent B, both contribute visually.

The desired definition remains:

> An element is displayed if it contributes non-zero visual information to at least one final output pixel.

### Version 1 required
Correct final visibility for normal opaque polygons, lines, points, markers, text, viewport clipping, complete occlusion, and partial occlusion.

### Version 1 best effort
Transparent and complex composited elements may initially use approximate semantics. Any approximation must be documented. The design must leave room for exact contributor tracking later.

## 12. Symbolizer-specific behavior
### Text
A label rejected by collision/placement logic produces no element. An accepted label becomes a render occurrence, but is returned only if some of it remains visible after later rendering.

### Markers
Each actual marker placement receives its own identity. Rejected placements produce no element. A later-covered placement is removed independently.

### Points
A point that rasterizes in the viewport may be displayed; if fully covered later, it is not.

### Lines
Roads and other lines are valid displayed elements. Visibility is based on rasterized output, not merely the source `LineString`.

### Polygons
Areas are valid displayed elements. A polygon fully covered later is absent; a partially visible polygon remains.

### Raster and other symbolizers
Use the same rule: return an element only if it contributes to final displayed output. Support may be incremental.

## 13. Public DisplayedElement
Conceptual owning result:

```cpp
struct displayed_element {
    std::uint64_t sequence;
    std::string layer_name;
    value_integer feature_id;
    display_element_type type;
    std::optional<box2d<double>> pixel_bbox;
    std::map<std::string, value> attributes;
};
```

The exact representation may follow Mapnik conventions. Results must remain valid after callbacks and after `apply()` completes.

## 14. Bounding boxes
When available, `pixel_bbox` uses output pixel coordinates.

Preferred long-term semantics: the bounding box of the element's **final visible pixels**, not merely its pre-occlusion extent.

Exact final bounds may be deferred in v1. If v1 reports pre-occlusion bounds, this must be documented.

## 15. Semantic identity, visible properties, and source metadata

Benchmark generation requires a strict separation between **object identity**, **visually expressed information**, and **source-only metadata**.

A datasource feature may contain many properties that are never expressed in the rendered image. These properties must not automatically become benchmark-visible information.

For example:

```json
{
  "name": "Berlin",
  "population": 3878100,
  "wikidata": "Q64",
  "elevation": 34
}
```

If the map displays only the label `Berlin`, then `population` and `elevation` must not be treated as visually available facts.

The representation should distinguish three categories.

### 15.1 Identity metadata

Identity metadata links a visible object back to its source feature or external entity.

Examples:

- Mapnik feature ID;
- layer name;
- database primary key;
- URI;
- OSM ID;
- Wikidata ID.

Identity metadata may be retained for dataset construction and evaluation even when the identifier itself is not printed on the map.

Identity metadata must be clearly marked as **non-visual metadata** unless it is itself rendered.

### 15.2 Visible properties

Visible properties are properties whose values are explicitly manifested in the rendered image.

The primary initial example is rendered text.

If a `TextSymbolizer` successfully renders:

```text
Berlin
```

and some of that label remains visible in the final image, the corresponding visible object may contain:

```json
{
  "visible_properties": {
    "label": "Berlin"
  }
}
```

If the label is rejected or fully occluded, that text must not appear as a visible property.

Future implementations may expose other visually encoded properties where their semantics can be determined reliably.

### 15.3 Source-only properties

Properties present in the datasource but not visually expressed are source metadata, not visual ground truth.

They may optionally be retained in a separate namespace for dataset construction, but they must never be mixed implicitly into `visible_properties`.

A benchmark consumer should be able to discard all source-only metadata without losing the representation of what is visible in the image.

### 15.4 Attribute collection

The observer may still need access to complete feature attributes to resolve identity or construct external annotations.

A conceptual opt-in API remains appropriate:

```cpp
virtual bool requires_all_attributes() const { return false; }
```

However, requesting an attribute from the datasource does **not** make that attribute visually observable.

The serialization layer must preserve the distinction between identity metadata, visible properties, and source-only properties.

## 16. VisibleObject structure

The benchmark-oriented semantic result should use an owning representation similar to:

```cpp
struct visible_object
{
    std::string layer_name;
    value_integer feature_id;

    // Non-visual identity information used to connect
    // this object to source data.
    std::map<std::string, value> identity_metadata;

    // Information actually manifested in the image.
    std::map<std::string, value> visible_properties;

    // Graphical manifestations that caused the object
    // to qualify as visible.
    std::vector<std::uint64_t> displayed_element_ids;

    std::optional<box2d<double>> visible_pixel_bbox;
};
```

The exact C++ representation may be adapted to Mapnik conventions.

A `VisibleObject` must never exist in the final result without at least one associated `DisplayedElement`.

The object should preserve enough source identity for deterministic benchmark construction while keeping that identity separate from visually expressed information.

### 16.1 Object geometry

A source object's full datasource geometry must not be interpreted as fully visible merely because part of the object appears in the map.

For example, a road or country may extend far outside the viewport.

The representation should distinguish:

```text
source geometry
```

from:

```text
visible image-space manifestation
```

For version 1, `visible_pixel_bbox` and the associated displayed elements are sufficient.

Future versions may expose:

- final visible screen-space geometry;
- pixel masks;
- visible pixel counts;
- visibility fractions.

These are preferable to exposing unclipped datasource geometry as though it were visible.

### 16.2 Semantic object type

Where a semantic object type such as `road`, `place`, `river`, or `building` is included, its provenance must be explicit.

If the type comes from source metadata rather than a graphical property directly inferable from the image, it must be treated as identity/source annotation rather than automatically as visually observable information.

This prevents benchmark gold data from leaking semantic facts that are not encoded in the map image.


## 17. Collector and finalization
Provide or demonstrate an owning collector:

```cpp
class visual_ground_truth_collector : public render_observer {
public:
    std::vector<displayed_element> const& displayed_elements() const;
    std::vector<visible_object> const& visible_objects() const;
};
```

The collector must copy retained data and must not store unsafe references to temporary rendering state.

After `renderer.apply()`, `displayed_elements()` must already represent **final graphical visibility** and `visible_objects()` must contain only source objects with at least one surviving displayed element.

The caller must not manually remove fully occluded elements or source features with no visible manifestation.

## 18. Performance and memory
When tracking is disabled:

- no visibility buffer should be allocated;
- no attributes should be copied solely for tracking;
- no bounds should be calculated solely for tracking;
- image output must be unchanged;
- performance impact should be negligible.

When enabled, extra memory/processing is expected. Avoid per-pixel dynamic allocation.

A full `uint32_t` ID buffer costs approximately:

```text
width * height * 4 bytes
```

The implementation should document its actual memory model.

## 19. Threading and exceptions
Observers do not need to be automatically thread-safe. If shared between simultaneous renders, synchronization is the caller's responsibility.

Observer exceptions may propagate through `agg_renderer::apply()` and abort rendering; this behavior should be documented.

## 20. Suggested implementation order
1. Add optional observer integration to `agg_renderer`.
2. Add internal element IDs and render sequence tracking.
3. Track the current layer.
4. Implement an opaque element-ID visibility mechanism.
5. Add polygon visibility.
6. Add line visibility.
7. Add point visibility.
8. Add text after successful placement.
9. Add individual marker placements.
10. Add final visibility resolution.
11. Add an owning `displayed_element_collector`.
12. Add optional complete attribute collection.
13. Extend remaining AGG symbolizers.
14. Investigate exact transparent/composited contributor tracking.

## 21. Required tests
### No-observer regression
Tracking disabled → existing rendered pixels remain identical.

### Complete occlusion
A rendered, B fully covers A → `[B]`.

### Partial occlusion
B covers only part of A → `[A, B]`.

### Rejected label
Collision rejects label → no text element.

### Covered label
Label accepted, then fully covered → no final text element.

### Partially covered label
Label accepted and partly visible → label remains.

### Off-screen geometry
No rasterized pixels in viewport → no element.

### Repeated markers
Three placements, one fully covered → only two visible placements returned.

### Multiple symbolizers
One feature with polygon, line, and text → independent elements for occurrences that remain visible.

### Layer identity
Same numeric feature ID in two layers → distinguishable results.

### Sequence
Sequence reflects original render order even when earlier elements disappear.

### Attribute collection
An attribute unused by styling is available when full attribute collection is requested.

### Cross-type occlusion
Test polygon-over-line, polygon-over-text, marker-over-polygon, and text-over-line.

### Benchmark object aggregation
One road feature renders casing, fill, and label. Expected: multiple displayed elements but exactly one visible object.

### Partially visible object
A feature has several graphical manifestations and only one survives occlusion. Expected: the visible object remains.

### Fully hidden object
All graphical manifestations of a feature are rejected, outside the viewport, or fully occluded. Expected: no visible object.

### Visible-property leakage
A feature contains `name`, `population`, and `uri`, but only `name` is rendered as text. Expected: `name` may appear in `visible_properties`; `population` must not. `uri` may appear only as explicitly non-visual identity metadata.

### Label visibility
A feature's label is rendered and remains visible. Expected: rendered label text is available as a visible property. If the label is fully occluded, that text is not a visible property unless another visible graphical manifestation independently expresses it.

## 22. Version 1 acceptance criteria
Version 1 is complete when:

- normal AGG rendering works unchanged without tracking;
- tracking is optional;
- image and element data come from the same render pass;
- each concrete rendering occurrence can have its own identity;
- layer and source feature are identifiable;
- point, line, polygon, text, and marker elements are supported;
- rejected placements do not create displayed elements;
- repeated marker placements are separate;
- complete opaque occlusion removes an element;
- partial opaque occlusion preserves an element;
- off-screen/non-rasterized elements are absent;
- final visibility resolution occurs before results are exposed;
- an owning collector retains results after rendering;
- displayed elements are aggregated into semantic visible objects by source feature;
- a visible object exists only when at least one associated displayed element survives;
- rendered text can be represented as a visible property;
- source-only attributes are not implicitly exposed as visible information;
- identity metadata is explicitly distinguished from visible properties;
- optional source metadata can be collected for dataset construction;
- opaque visibility and benchmark leakage behavior have automated tests.

## 23. Explicit non-goals for version 1
- Exact attribution through every transparency, blending, filter, group-opacity, and compositing case.
- Pixel → element hit testing.
- Exact final visible screen-space geometry.
- Exact final bounding boxes for every symbolizer.
- Python bindings.

These may be added after the C++ API and opaque visibility semantics stabilize.

## 24. Future extensions
Potential additions include:

```cpp
displayed_element.pixel_geometry
displayed_element.rendered_text
displayed_element.opacity
displayed_element.composite_mode
displayed_element.style_name
displayed_element.rule_name
```

Other future capabilities:

- exact transparent contributor tracking;
- final visible screen-space geometry;
- pixel-to-element hit testing;
- spatial indexing;
- Python/Node bindings;
- semantic/Linked Data identifiers;
- visible pixel count or visibility fraction.

## 25. Example target usage

```cpp
mapnik::Map map = ...;
mapnik::image_rgba8 image(map.width(), map.height());

mapnik::visual_ground_truth_collector collector;

mapnik::agg_renderer<mapnik::image_rgba8> renderer(map, image);
renderer.set_render_observer(&collector);
renderer.apply();

mapnik::save_to_file(image, "map.png", "png");

for (auto const& object : collector.visible_objects())
{
    std::cout
        << object.layer_name << " "
        << object.feature_id << "\n";
}
```

Conceptual machine-readable output:

```json
{
  "objects": [
    {
      "identity": {
        "layer": "roads",
        "feature_id": 10
      },
      "visible_properties": {
        "label": "A12"
      },
      "displayed_elements": [1, 2, 3]
    },
    {
      "identity": {
        "layer": "places",
        "feature_id": 20,
        "uri": "https://example.org/place/20"
      },
      "visible_properties": {
        "label": "Berlin"
      },
      "displayed_elements": [4, 5]
    }
  ]
}
```

The URI above is identity metadata. Its presence in the machine-readable representation does not imply that the URI text is visible in the map image.

## 26. Central invariants

The extension is intended to generate **visual ground truth**, not a dump of the underlying datasource.

The implementation must preserve all of the following:

> **Rendered is not equivalent to displayed.**

An element completely covered in final output is not a `DisplayedElement`.

> **Present in the datasource is not equivalent to visible in the map.**

A source feature with no surviving graphical manifestation is not a `VisibleObject`.

> **Available as source metadata is not equivalent to visually observable information.**

A property may appear in benchmark-visible information only when its value is actually manifested by the rendered map under the supported semantics.

The final relationship is:

```text
Map image
   |
   +-- DisplayedElement[]  = graphical ground truth
   |
   +-- VisibleObject[]     = semantic objects grounded in those displayed elements
          |
          +-- identity metadata      (may be non-visual)
          +-- visible properties     (must be image-grounded)
```

The benchmark gold representation should normally be derived from `VisibleObject[]` and `visible_properties`, using identity metadata only for linking and evaluation.