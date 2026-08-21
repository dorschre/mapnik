# CartoGraph Map Representation Ontology (`cg:`) + Provenance Vocabulary (`cgp:`)

An RDF/OWL 2 DL vocabulary pair and SHACL validation suite that connects **geospatial source entities** (features, geometries, and domain attributes) to the **shown display elements on the rendered map image** (strokes, fills, text labels, markers, compound shields, and pixel geometries), and records the **production provenance** of how those elements were generated.

The vocabulary is split into **two independently publishable modules**:

| Module | Namespace | File | Concern |
|---|---|---|---|
| **Representation** | `https://w3id.org/cartograph#` (`cg:`) | `cartograph.ttl` | The *static* model of what is shown: features, visible objects, canvas display elements, portrayals. |
| **Provenance** | `https://w3id.org/cartograph-provenance#` (`cgp:`) | `cartograph-provenance.ttl` | The *production* lifecycle aligned to W3C PROV-O: plans, activities, input entities, derivation root. |

`cartograph.ttl` **imports** `cartograph-provenance.ttl` — representation depends on provenance, not vice-versa.

---

## 1. Core Architecture: The Two-Layer Model

The representation vocabulary strictly separates the **Spatial Canvas Geometric Primitive** from its **Visual Representation (Portrayal & Composition)**:

```text
1. Geospatial Feature (geo:Feature)
   └── Geographic Geometry (geo:Geometry in CRS84) + Datasource Metadata (OSM tags, etc.)
       │
2. Visible Object (cg:VisibleObject)
   └── Semantic presence of a feature on a specific MapImage (cg:inMap)
       ├── Visible properties (e.g. cg:visibleLabel)
       └── Aggregates all surviving DisplayElements
           │
3. Canvas Geometric DisplayElements (0D, 1D, 2D Screen Space Primitives)
   │  ├── cg:CandidateElement   (all occurrences from rule application, incl. non-survivors)
   │  │     └── cg:DisplayElement (survivors with >= 1 visible pixel)
   │  │           ├── cg:PointElement   (0D Discrete Pixel Anchor: [x, y])
   │  │           ├── cg:LineElement    (1D Screen Polyline / Curve Path)
   │  │           └── cg:PolygonElement (2D Screen Planar Region / Boundary)
   │
   └── Visual Representations (cg:hasPortrayal -> cg:Portrayal)
      ├── Atomic Portrayals:
      │   ├── cg:StrokePortrayal  (line/boundary stroke: color, width, dash)
      │   ├── cg:FillPortrayal    (surface interior fill: color, opacity)
      │   ├── cg:MarkerPortrayal  (pictorial PNG/SVG icon symbol)
      │   ├── cg:DotPortrayal     (geometric circle/dot symbol)
      │   ├── cg:TextPortrayal    (rendered text string, font, halo)
      │   └── cg:PatternPortrayal (repeated texture / pattern tile)
      │
      └── cg:CompoundPortrayal (Composed Visual Representations):
          ├── cg:ShieldPortrayal          (= cg:MarkerPortrayal + cg:TextPortrayal)
          ├── cg:CasedLinePortrayal       (= Casing cg:StrokePortrayal + Fill cg:StrokePortrayal)
          └── cg:ComposedSurfacePortrayal (= cg:FillPortrayal + cg:StrokePortrayal + optional label/pattern)
```

A compound portrayal may be attached to a single element (shield, composed surface) **or** be a floating grouping whose components are carried by separate elements (cased line: casing and fill are two independent strokes with their own `pixelCount` and `visibilityStatus`).

## 2. Two Orthogonal Views & the Derivation Chain

The model spans two orthogonal axes that meet at the `DisplayElement` node:

```text
   AXIS 1 (representation)   geo:Feature ──cg:depictsFeature──▶ DisplayElement   "what is shown"
   AXIS 2 (provenance)       DisplayElement ──wasGeneratedBy──▶ Activity ──▶ Plan/Agent   "how it was made"
```

The feature-derivation link is a **three-level broad→narrow chain** across the two namespaces:

```text
prov:wasDerivedFrom                        [PROV-O]
  └── cgp:derivedFromFeature               [cartograph-provenance#]   any entity from a geo:Feature
        └── cg:depictsFeature              [cartograph#]   canvas depiction
        └── cg:representsFeature           [cartograph#]   visible semantic object
```

Because `cg:depictsFeature ⊑ cgp:derivedFromFeature ⊑ prov:wasDerivedFrom`, every depiction *is* a derivation (verified by OWL-RL inference). `cgp:derivedFromFeature` is broader than the representation predicates: it also covers non-visual derivations (e.g. a CRS-transformed geometry never shown on canvas).

---

## 3. Ontology Namespaces & Prefixes

```turtle
@prefix cg:   <https://w3id.org/cartograph#> .
@prefix cgp:  <https://w3id.org/cartograph-provenance#> .
@prefix geo:  <http://www.opengis.net/ont/geosparql#> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
```

---

## 4. Class Hierarchy

### A. Representation Infrastructure (`cg:`)
- `cg:MapImage` (subclass of `prov:Entity`): Rendered map artifact ($M$) with `canvasWidthPx`, `canvasHeightPx`, `zoomLevel`, `crs`, `viewportWkt`, `backgroundColor`.
- `cg:MapLayer`: Logical stylesheet layer (`layerName`, `layerOrder`).
- `cg:VisibleObject` (subclass of `prov:Entity`): Semantic object present in a map image. Grounded by having $\ge 1$ surviving `DisplayElement`.

### B. Canvas Geometric Display Elements ($0\text{D}, 1\text{D}, 2\text{D}$) — `cg:`
- `cg:CandidateElement` (subclass of `prov:Entity`): Root of canvas spatial occurrences produced by rule application (stage $S_E$). Covers both survivors and non-surviving candidates (suppressed, occluded, outside viewport).
- `cg:DisplayElement` (subclass of `cg:CandidateElement`): A candidate that survived visibility resolution and contributes $\ge 1$ visible pixel.
  - `cg:PointElement`: $0\text{D}$ point anchor at pixel $[x, y]$.
  - `cg:LineElement`: $1\text{D}$ polyline curve in canvas pixel space.
  - `cg:PolygonElement`: $2\text{D}$ planar surface region in canvas pixel space.

### C. Visual Portrayals & Compound Portrayals — `cg:`
- `cg:Portrayal`: Root of visual graphic representations.
  - `cg:StrokePortrayal`: Stroked path (`strokeColor`, `strokeWidthPx`, `dashPattern`).
  - `cg:FillPortrayal`: Surface interior fill (`fillColor`, `opacity`).
  - `cg:MarkerPortrayal`: Pictorial icon symbol (`iconName`).
  - `cg:DotPortrayal`: Geometric dot shape (`fillColor`, `sizePx`, `symbolShape`).
  - `cg:TextPortrayal`: Rendered text (`renderedText`, `fontFamily`, `fontSizePx`, `textColor`, `haloColor`, `haloRadiusPx`).
  - `cg:PatternPortrayal`: Pattern / texture tile (`patternName`).
  - `cg:RasterPortrayal`: Raster imagery patch.
  - `cg:CompoundPortrayal` (subclass of `cg:Portrayal`): Composed visual representation with `cg:hasComponent`.
    - `cg:ShieldPortrayal`: Composite point marker (`cg:hasIconComponent` + `cg:hasTextComponent`).
    - `cg:CasedLinePortrayal`: Composite road stroke (`cg:hasCasingStroke` + `cg:hasFillStroke`).
    - `cg:ComposedSurfacePortrayal`: Composite polygon surface (`cg:hasFillComponent` + `cg:hasBoundaryStroke`).

### D. Provenance & Production Lifecycle — `cgp:` (W3C PROV-O Alignment)
- `cgp:derivedFromFeature` (subproperty of `prov:wasDerivedFrom`): the feature-derivation root specialized by `cg:depictsFeature` / `cg:representsFeature`.
- `cgp:PortrayalRule` (subclass of `prov:Plan`, `prov:Entity`): Declarative rule specification ($R$) selecting features and assigning symbolizers.
- `cgp:LayoutPolicy` (subclass of `prov:Plan`, `prov:Entity`): Layout, placement, collision, and suppression policy plan ($L$).
- `cgp:MapConfiguration` (subclass of `prov:Entity`): View parameters, extent, and resolution entity ($O$).
- `cgp:ResourceSet` (subclass of `prov:Entity`): Pinned font and icon asset collection ($X$).
- `cgp:ReferenceAnswer` (subclass of `prov:Entity`): Gold reference answer entity ($A$).
- **Activities**:
  - `cgp:RuleApplicationActivity` (subclass of `prov:Activity`): $f_{\mathrm{rule}}$ execution producing candidate display elements ($S_E$).
  - `cgp:LayoutResolutionActivity` (subclass of `prov:Activity`): $f_{\mathrm{resolve}}$ execution resolving collisions into final map content ($S_F$).
  - `cgp:MapRenderingActivity` (subclass of `prov:Activity`): $f_{\mathrm{render}}$ execution producing the map image ($M$).
  - `cgp:AnswerDerivationActivity` (subclass of `prov:Activity`): $q_j$ execution producing the reference answer ($A$).

---

## 5. Key Relationships

| Predicate | Domain | Range | Description |
|---|---|---|---|
| `cgp:derivedFromFeature` | `prov:Entity` | `geo:Feature` | Feature-derivation root (`rdfs:subPropertyOf prov:wasDerivedFrom`). |
| `cg:depictsFeature` | `cg:DisplayElement` | `geo:Feature` | Canvas depiction — specialized feature derivation. |
| `cg:representsFeature` | `cg:VisibleObject` | `geo:Feature` | Visible semantic object → source feature (specialized derivation). |
| `cgp:usedRule` | `prov:Entity` | `cgp:PortrayalRule` | Links display element to the portrayal rule plan (`prov:Plan`) that justified it. |
| `cgp:usedPolicy` | `rdfs:Resource` | `cgp:LayoutPolicy` | Links map run to the layout policy plan (`prov:Plan`). |
| `cgp:usedResources` | `rdfs:Resource` | `cgp:ResourceSet` | Links map run to pinned graphical assets. |
| `cgp:usedConfiguration` | `rdfs:Resource` | `cgp:MapConfiguration` | Links map run to the configuration entity. |
| `cgp:pinnedAsset` | `rdfs:Resource` | `xsd:string` | A pinned font/icon asset in a `cgp:ResourceSet`. |
| `cg:featureType` | `geo:Feature` | `xsd:string` | Portable semantic type tag (e.g. `"road"`, `"hospital"`). |
| `cg:scaleDenominator` | `rdfs:Resource` | `xsd:decimal` | Representative-fraction denominator for non-tiled renders. |
| `cg:visiblePixelBbox` | `cg:VisibleObject` | `xsd:string` | Bbox of actually visible pixels (contrast `cg:pixelBbox`). |
| `cg:hasPortrayal` | `cg:DisplayElement` | `cg:Portrayal` | Connects geometric element to its visual portrayal. |
| `cg:hasComponent` | `cg:CompoundPortrayal` | `cg:Portrayal` | Links compound portrayal to constituent sub-portrayals. |
| `cg:hasCasingStroke` | `cg:CasedLinePortrayal` | `cg:StrokePortrayal` | Casing stroke of a cased line. |
| `cg:hasFillStroke` | `cg:CasedLinePortrayal` | `cg:StrokePortrayal` | Interior fill stroke of a cased line. |
| `cg:hasIconComponent` | `cg:ShieldPortrayal` | `cg:MarkerPortrayal` | Icon badge component of a shield. |
| `cg:hasTextComponent` | `cg:ShieldPortrayal` | `cg:TextPortrayal` | Route text component of a shield. |
| `cg:partOfVisibleObject` | `cg:DisplayElement` | `cg:VisibleObject` | Aggregates display elements into the visible object. |
| `cg:inMap` | `rdfs:Resource` | `cg:MapImage` | Scopes elements and objects to the map image. |

---

## 6. Competency Questions & Executable SPARQL Queries

All queries are verified against the Nuremberg test data:

- **CQ1 (`cq1-feature-for-display-element.rq`)**: Given a display element, retrieve the source geospatial feature, its human label, and its geographic CRS84 geometry.
- **CQ2 (`cq2-display-elements-for-feature.rq`)**: Given a geospatial feature (e.g. `feat_neutorstrasse`), retrieve all its visual display elements, their draw orders, and portrayals.
- **CQ3 (`cq3-visible-street-names.rq`)**: Retrieve all street names visually rendered on the map (excluding collision-suppressed names like *Kollisionsgasse*). Uses the portable `cg:featureType "road"` tag rather than a fixture-specific class.
- **CQ4 (`cq4-intersecting-roads-in-view.rq`)**: Retrieve all roads shown intersecting with a given road on the map based on visible manifestations.
- **CQ5 (`cq5-visible-vs-source-properties.rq`)**: Distinguish visually manifested properties (e.g. building label) from non-visual source metadata (e.g. Wikidata ID, elevation), verifying zero leakage for VQA benchmarks.
- **CQ6 (`cq6-element-visual-descriptions.rq`)**: List all display elements with their pixel counts, draw orders, layers, and visual portrayals.
- **CQ7 (`cq7-objects-by-symbol.rq`)**: Retrieve all objects and optionally their names that use a specific icon/symbol (e.g., `"amenity-hospital"`).
- **CQ8 (`cq8-elements-by-spatial-dimension.rq`)**: Retrieve display elements grouped by their topological anchor dimension ($0\text{D}$ point, $1\text{D}$ curve, $2\text{D}$ surface).
- **CQ9 (`cq9-compound-portrayals.rq`)**: Retrieve all compound portrayals and their constituent sub-components, whether attached to a single element (shield, composed surface) or spanning several elements (cased line).
- **CQ10 (`cq10-provenance-and-portrayal-rules.rq`)**: Provenance traceability — Retrieve display elements, source features, and the portrayal rule plans (`prov:Plan`) that justified them.
- **CQ11 (`cq11-rendering-lineage.rq`)**: Auditable lineage — Trace the mediated chain element → `prov:wasGeneratedBy` activity → `prov:qualifiedAssociation` → plan/agent, constrained by the element's `cgp:usedRule`.

---

## 7. Pixel Coordinate Convention

All canvas pixel coordinates (`cg:pixelBbox`, `cg:visiblePixelBbox`, `cg:pixelAnchorX/Y`, `cg:pixelGeometry`) follow one convention:

- **Origin** at the top-left corner of the image.
- **Y axis** increases downward.
- Coordinates refer to **pixel corners** (not centres).
- Pixel boxes are **half-open** `[min, max)`.

## 8. File Layout

```text
map-display-ontology/
  cartograph.ttl                  # representation vocabulary (imports cartograph-provenance)
  cartograph-provenance.ttl       # provenance vocabulary (imports PROV-O)
  cartograph-shapes.ttl           # SHACL validation shapes (representation invariant checks)
  examples/
    nuremberg-sample-data.ttl     # conforming positive fixture
    invalid-leakage-example.ttl   # non-conforming negative fixture
    candidate-element-minimal.ttl # regression: bare suppressed candidate conforms under rdfs & owlrl
  queries/
    cq01..cq11.rq                 # executable competency queries
```

---

## 9. Quality Assurance & Validation Summary

1. **`lint`**: 0 errors, 0 warnings — 85 representation terms + 15 provenance terms.
2. **`reason` (OWL-RL)**: Materialized inferred triples (including the `cg:depictsFeature ⊑ cgp:derivedFromFeature ⊑ prov:wasDerivedFrom` chain) without contradictions.
3. **`validate` (pySHACL)**: Full conformance on positive data under **both** `inference=none` and `inference=rdfs` (the RDFS run previously failed because `cg:crs`/`cg:zoomLevel`/`cg:viewportWkt` declared `rdfs:domain cg:MapImage`, which wrongly inferred `MapConfiguration` to be a `MapImage`; the domains were dropped). Strict violation detection on negative fixtures.

**Round-2 fix (CandidateElement reachability under reasoning).** A bare `cg:CandidateElement` was still unreachable once it stated `cg:depictsFeature` (required by `CandidateElementShape`): three properties (`cg:depictsFeature`, `cg:hasPortrayal`, `cg:partOfVisibleObject`) declared `rdfs:domain cg:DisplayElement`, so stating the required property inferred the candidate to be a `DisplayElement` and the strict shape fired. These domains were moved up to `cg:CandidateElement`. That alone only fixes `inference=rdfs`; under `inference=owlrl` the candidate was still inferred a `DisplayElement` via the inverse chain, so the ranges of the two inverse properties (`cg:hasDisplayElement`, `cg:hasDisplayedElement`) were widened to `cg:CandidateElement` as well. The central "no ungrounded visible object" invariant is still enforced by `VisibleObjectShape`'s `sh:class cg:DisplayElement` SHACL constraint (not by the OWL range). `candidate-element-minimal.ttl` is the minimal regression fixture, verified to conform under none, rdfs, and owlrl.
4. **`query` (SPARQL)**: Automated verification of all 11 competency queries passing with exact assertions.

## 10. Resolved Design Decision: Cased-Line Stroke Attribution

A reviewer noted that in a real renderer the casing and fill of a cased road are **two independent occurrences** that occlude each other and can each disappear on its own (e.g. 71 px casing vs 126 px fill), so folding them into one `cg:CasedLinePortrayal` on one element made per-stroke pixel attribution unrepresentable.

**Resolution (Option A):** a compound portrayal may span several `DisplayElement`s. The casing and fill are now two independent `cg:LineElement`s, each with its own `pixelCount` and `visibilityStatus`; the `CasedLinePortrayal` is a floating grouping whose components (`cg:hasCasingStroke` / `cg:hasFillStroke`) are carried by those two elements. `ShieldPortrayal` and `ComposedSurfacePortrayal` remain attached to a single element because their parts are genuinely co-located and drawn together. Consequence: a cased road now yields two elements instead of one, so consumers group by the compound (or the `VisibleObject`) to see them as one cased line.
