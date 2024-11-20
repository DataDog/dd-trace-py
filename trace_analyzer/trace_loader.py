import json

def parse_trace(trace_file):
    print("Loading trace file: %s" % trace_file)
    traces = []
    raw_traces = []
    with open(trace_file) as f:
        raw_traces = json.load(f)
    
    for i, raw_trace in enumerate(raw_traces):
        trace = Trace.from_json(raw_trace)
        traces.append(trace)

    return traces


class Span:
    def __init__(self, span_id=None, service=None):
        self.span_id = span_id
        self.service = service

    @classmethod
    def from_json(cls, raw_span):
        return cls(span_id=raw_span["span_id"], service=raw_span["service"])

    def __repr__(self):
        return "Span(%s)" % self.span_id


class Trace:
    def __init__(self, trace_id=None):
        self.spans = []
        self.trace_id = trace_id

    @property
    def root_span(self):
        return self.spans[0]
    
    @classmethod
    def from_json(cls, raw_trace):
        trace = Trace(trace_id=raw_trace[0]["trace_id"])
        for raw_span in raw_trace:
            span = Span.from_json(raw_span)
            trace.add(span)
        
        return trace
    
    def add(self, span: Span):
        self.spans.append(span)

    def __repr__(self):
        return "Trace(%s)" % self.trace_id