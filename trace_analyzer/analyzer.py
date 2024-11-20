#!/usr/bin/env python

import json
from pprint import pprint
from rules import apply_rules
from trace_loader import parse_trace
from semantic_graph import Graph


def analyze(trace_file, tracer_configuration):
    print("With configuration file: %s" % tracer_configuration)

    traces = parse_trace(trace_file)

    tracer_configuration = None
    with open(tracer_configuration_file) as f:
        tracer_configuration = json.load(f)
    
    print("Tracer configuration: %s" % tracer_configuration)

    for i, trace in enumerate(traces, start=1):
        print(f"Trace: ({i}/{len(traces)})")
        print(trace)

    graph = Graph.from_traces(traces)
    breakpoint()

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Trace Analyzer")
    parser.add_argument("-t", "--trace-file", help="The trace file to analyze", required=True, action="store")
    parser.add_argument("-c", "--tracer-configuration-file", help="The tracer configuration to use", required=True, action="store")
    args = parser.parse_args()
    trace_json_file = args.trace_file
    tracer_configuration_file = args.tracer_configuration_file

    analyze(trace_json_file, tracer_configuration_file)
