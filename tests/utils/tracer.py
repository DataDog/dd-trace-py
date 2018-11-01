from ddtrace.encoding import JSONEncoder, MsgpackEncoder
from ddtrace.tracer import Tracer
from ddtrace.writer import AgentWriter

from .span import TestSpan


class DummyWriter(AgentWriter):
    """ DummyWriter is a small fake writer used for tests. not thread-safe. """

    def __init__(self):
        # original call
        super(DummyWriter, self).__init__()
        # dummy components
        self.spans = []
        self.traces = []
        self.services = {}
        self.json_encoder = JSONEncoder()
        self.msgpack_encoder = MsgpackEncoder()

    def write(self, spans=None, services=None):
        if spans:
            # the traces encoding expect a list of traces so we
            # put spans in a list like we do in the real execution path
            # with both encoders
            trace = [spans]
            self.json_encoder.encode_traces(trace)
            self.msgpack_encoder.encode_traces(trace)
            self.spans += spans
            self.traces += trace

        if services:
            self.json_encoder.encode_services(services)
            self.msgpack_encoder.encode_services(services)
            self.services.update(services)

    def pop(self):
        # dummy method
        s = self.spans
        self.spans = []
        return s

    def pop_traces(self):
        # dummy method
        traces = self.traces
        self.traces = []
        return traces

    def pop_services(self):
        # dummy method
        s = self.services
        self.services = {}
        return s


class DummyTracer(Tracer):
    def __init__(self, *args, **kwargs):
        super(DummyTracer, self).__init__(*args, **kwargs)
        self.writer = DummyWriter()

    def start_span(self, *args, **kwargs):
        span = super(DummyTracer, self).start_span(*args, **kwargs)
        return TestSpan(span)
