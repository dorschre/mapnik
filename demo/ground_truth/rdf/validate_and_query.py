#!/usr/bin/env python3
"""Validate a CartoGraph ground-truth graph and run the demo queries.

    python3 validate_and_query.py berlin.ttl --ontology map-display-ontology

Runs pySHACL against the ontology's own shapes, with and without RDFS
reasoning (the two disagree if a domain declaration pulls an individual into an
unintended class), then executes every .rq file in queries/.
"""

import argparse
import glob
import os
import re
import sys
from collections import Counter

try:
    from pyshacl import validate
    from rdflib import Graph
except ImportError:
    sys.exit("this tool needs rdflib and pyshacl: pip install rdflib pyshacl")


def load_ontology(directory):
    graph = Graph()
    for name in ("cartograph-provenance.ttl", "cartograph.ttl"):
        graph.parse(os.path.join(directory, name), format="turtle")
    return graph


def run_validation(data_path, ontology_dir):
    ontology = load_ontology(ontology_dir)
    shapes = Graph().parse(os.path.join(ontology_dir, "cartograph-shapes.ttl"), format="turtle")
    ok = True
    for inference in ("none", "rdfs"):
        data = Graph().parse(data_path)
        conforms, _, text = validate(
            data, shacl_graph=shapes, ont_graph=ontology, inference=inference, advanced=True
        )
        print("SHACL (inference={:5}): {}".format(inference, "conforms" if conforms else "VIOLATIONS"))
        if not conforms:
            ok = False
            for message, count in Counter(re.findall(r"Message: (.*)", text)).most_common(10):
                print("    {:5}x {}".format(count, message))
    return ok


def run_queries(data_path, query_dir, limit, ontology_dir):
    graph = Graph().parse(data_path)
    data_triples = len(graph)
    # The vocabulary is merged in so that rdfs:subClassOf paths and the SKOS
    # concept labels resolve; plain SPARQL does no inference of its own.
    graph += load_ontology(ontology_dir)
    print("\ngraph: {} data triples (+{} from the vocabulary)".format(data_triples, len(graph) - data_triples))
    for path in sorted(glob.glob(os.path.join(query_dir, "*.rq"))):
        with open(path) as handle:
            text = handle.read()
        comment = " ".join(line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#"))
        print("\n=== {} ".format(os.path.basename(path)) + "=" * 20)
        if comment:
            print("    {}".format(comment))
        rows = list(graph.query(text))
        print("    {} row(s)".format(len(rows)))
        for row in rows[:limit]:
            print("      " + " | ".join("" if v is None else str(v).rsplit("/", 1)[-1] for v in row))
        if len(rows) > limit:
            print("      ... {} more".format(len(rows) - limit))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("graph", help="the .ttl produced by ground_truth_to_rdf.py")
    parser.add_argument("--ontology", default="map-display-ontology", help="directory holding the ontology")
    parser.add_argument("--queries", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "queries"))
    parser.add_argument("--rows", type=int, default=6, help="rows to print per query")
    parser.add_argument("--skip-validation", action="store_true")
    opts = parser.parse_args()

    ok = True
    if not opts.skip_validation:
        ok = run_validation(opts.graph, opts.ontology)
    run_queries(opts.graph, opts.queries, opts.rows, opts.ontology)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
