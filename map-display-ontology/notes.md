
Issues in the ontology

1. Your own positive fixture fails under reasoning. cg:crs, cg:zoomLevel and cg:viewportWkt declare rdfs:domain cg:MapImage, but cgp:MapConfiguration carries them — so RDFS infers the configuration is a MapImage and it then fails cg:MapImageShape:

nuremberg-sample-data.ttl, inference=none : conforms=True
nuremberg-sample-data.ttl, inference=rdfs : conforms=False
   1x A MapImage must have exactly one canvasHeightPx >= 1.
   1x A MapImage must have exactly one canvasWidthPx >= 1.

Your QA notes say reasoning and validation both pass — they were almost certainly run separately. Fix: drop rdfs:domain from those three, or hang them off a shared cg:Viewported superclass.

2. Two terms are used but never declared. cg:visiblePixelBbox (on VisibleObject in the Nuremberg fixture) and cg:pinnedAsset (on ResourceSet) appear in no .ttl. I emit the declared cg:pixelBbox instead.

3. The VisibilityStatus vocabulary is unreachable. cg:Suppressed, cg:Occluded and cg:OutsideViewport all describe elements with no surviving pixels — but cg:DisplayElementShape demands cg:pixelCount >= 1 and at least one portrayal. No conforming DisplayElement can ever carry those three values. This is not hypothetical: on that tile 3088 of 3696 occurrences left no visible pixel, and I currently cannot represent a single one. Either relax to minInclusive 0, or add a cg:CandidateElement superclass and apply the strict shape only to survivors.

4. cg:CasedLinePortrayal assumes casing and fill are one element. In a renderer they are two independent occurrences that occlude each other — the fill covers most of the casing, and each can disappear on its own (above: 71 px vs 126 px). Folding them into one portrayal on one element makes pl attribution unrepresentable. Either let a compound portrayal span several
rdf/*.ttl
rdf/*.nt
rdf/*.jsonld

Done — and yes, I found real issues in the ontology.

python3 demo/ground_truth/rdf/ground_truth_to_rdf.py osm/pariser-platz.json \
    -o rdf/pariser-platz.ttl --base https://example.org/berlin/ \
    --source-geojson demo/ground_truth/osm
python3 demo/ground_truth/rdf/validate_and_query.py rdf/pariser-platz.ttl

140359 triples · 608 display elements · 463 visible objects
SHACL (inference=none ): conforms
SHACL (inference=rdfs ): conforms

New files: rdf/ground_truth_to_rdf.py, rdf/validate_and_query.py, rdf/queries/q1..q6.rq.

To reach the ontology I had to widen the extractor — it only emitted element ids, never their types, styling or geometry. The JSON now carries a map block and a full elements[] array, including the styling read straight off each symbolizer. One road object comes out as:

roads 164 'Ebertstraße'
  2776 line  stroke #c08040 12px      71 px visible   (casing)
  2900 line  stroke #fcd6a4 8.5px    126 px visible   (fill)
  3244 text  DejaVu 11px, halo 1.5   708 px visible   (line-placed label)

Issues in the ontology

1. Your own positive fixture fails under reasoning. cg:crs, cg:zoomLevel and cg:viewportWkt declare rdfs:domain cg:MapImage, but cgp:MapConfiguration carries them — so RDFS infers the configuration is a MapImage and it then fails cg:MapImageShape:

nuremberg-sample-data.ttl, inference=none : conforms=True
nuremberg-sample-data.ttl, inference=rdfs : conforms=False
   1x A MapImage must have exactly one canvasHeightPx >= 1.
   1x A MapImage must have exactly one canvasWidthPx >= 1.

Your QA notes say reasoning and validation both pass — they were almost certainly run separately. Fix: drop rdfs:domain from those three, or hang them off a shared cg:Viewported superclass.

2. Two terms are used but never declared. cg:visiblePixelBbox (on VisibleObject in the Nuremberg fixture) and cg:pinnedAsset (on ResourceSet) appear in no .ttl. I emit the declared cg:pixelBbox instead.

3. The VisibilityStatus vocabulary is unreachable. cg:Suppressed, cg:Occluded and cg:Outments with no surviving pixels — but cg:DisplayElementShape demands cg:pixelCount >= 1and at least one portrayal. No conforming DisplayElement can ever carry those three values. This is not hypothetical: on that tile 3088 of 3696 occurrences left no visible pixel, and I currently cannot represent a single one. Either relax to minInclusive 0, or add a cg:CandidateElement superclass and apply the strict shape only to survivors.

4. cg:CasedLinePortrayal assumes casing and fill are one element. In a renderer they are two independent occurrences that occlude each other — the fill covers most of the casing, and each can disappear on its own (above: 71 px vs 126 px). Folding them into one portrayal on one element makes per-stroke visibility and pixel attribution unrepresentable. Either let a compound portrayal span several
DisplayElements, or state that casing/fill grouping is the VisibleObject's job.

5. cg:zoomLevel is mandatory but only meaningful for slippy tiles. An arbitrary extent-and-size render has a scale, not a zoom. I derive one from web-mercator resolution; for any other CRS it is undefined and I'd have to fabricate it. Make it optional or add cg:scaleDenominator.

6. geo:hasGeometry mandatory on every geo:Feature fights the ontology's own purpose. Vis withholds unclipped source geometry — that's the "in the datasource ≠ visible in themap" invariant. Conforming forces a join back to the datasource, which is why my script needs --source-geojson. A pure ground-truth graph cannot satisfy this shape.

7. The pixel coordinate convention is undocumented. Nothing says the origin is top-left with y downward, nor whether coordinates are pixel centres or corners. I use top-left, y-down, corners, half-open
boxes — but a consumer can't know that.

8. Smaller things. cg:pixelCount and cg:visiblePixelCount share the identical rdfs:label "visible pixel count". cg:anchorDimension (integer) and cg:hasAnchorDimension (SKOS concept) state the same fact
twice with no axiom linking them, so they can silently disagree. There is no cg: way to building — your examples mint ex:RoadFeature in the data namespace, and CQ3 depends onit, so that shipped query only runs against that one fixture. All eleven CQs hard-code fixture URIs, which makes them regression tests rather than portable competency questions; rdf/queries/ has parameter-free equivalents.

Building the graph is like writing a museum catalogue from the gallery rather than the storeroom: an object gets an entry only if a visitor could actually see it, and the crate it arrived in is recorded separately. The comparison stops working here, because a museum can fetch a crated object on request, whereas an occluded element left no trace in the image to fetch.

Two fixes on my side along the way: JSON null attributes were becoming the literal string "None" in RDF, and projected extents were losing precision to the default six significant digits. Test suite still green: 90 cases, 29155 assertions.


# Round 2
One follow-up bug, same shape as issue #1. cg:CandidateElement can't actually be used under reasoning:

bare candidate, inference=none : conforms=True
bare candidate, inference=rdfs : conforms=False
     A surviving DisplayElement must have a positive pixelCount.
     A DisplayElement must have at least one visual portrayal (cg:hasPortrayal).

cg:depictsFeature, cg:hasPortrayal and cg:partOfVisibleObject still declare rdfs:domain cg:DisplayElement (lines 154, 181, 194). But cg:CandidateElementShape requires cg:depictsFeature — so stating it makes RDFS infer the candidate is a DisplayElement, and the strict shape then fires. The class is unreachable exactly as cg:Suppressed was before. Fix is three lines: move those domains up to cg:CandidateElement. That six-triple fixture above reproduces it. Until then --candidates is opt-in in my converter, so the default output stays clean.

# Round 2 — RESOLVED
Fixed. Moved the domains of cg:depictsFeature, cg:hasPortrayal, cg:partOfVisibleObject up to
cg:CandidateElement as suggested. Note: that alone only fixes inference=rdfs. Because
cg:hasDisplayElement (inverse of depictsFeature) and cg:hasDisplayedElement (inverse of
partOfVisibleObject) still declared rdfs:range cg:DisplayElement, OWL-RL re-inferred a bare
candidate as a DisplayElement via the inverse+range chain. Both ranges were therefore widened
to cg:CandidateElement. The "no ungrounded visible object" invariant is preserved because
VisibleObjectShape still enforces sh:class cg:DisplayElement on cg:hasDisplayedElement.
Regression fixture: examples/candidate-element-minimal.ttl (conforms under none, rdfs, and owlrl).
