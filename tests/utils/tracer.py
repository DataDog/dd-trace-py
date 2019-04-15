from collections import deque
from ddtrace.encoding import JSONEncoder, MsgpackEncoder
from ddtrace.tracer import Tracer
from ddtrace.writer import AgentWriter
from ddtrace.compat import PY3


class DummyWriter(AgentWriter):
    """DummyWriter is a small fake writer used for tests. not thread-safe."""

    def __init__(self, *args, **kwargs):
        # original call
        super(DummyWriter, self).__init__(*args, **kwargs)

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

        # Setting service info has been deprecated, we want to make sure nothing ever gets written here
        assert self.services == {}
        s = self.services
        self.services = {}
        return s


class DummyTracer(Tracer):
    """
    DummyTracer is a tracer which uses the DummyWriter by default
    """
    def __init__(self):
        super(DummyTracer, self).__init__()
        self._update_writer()

    def _update_writer(self):
        self.writer = DummyWriter(
                hostname=self.writer.api.hostname,
                port=self.writer.api.port,
                filters=self.writer._filters,
                priority_sampler=self.writer._priority_sampler,
        )

    def configure(self, *args, **kwargs):
        super(DummyTracer, self).configure(*args, **kwargs)
        # `.configure()` may reset the writer
        self._update_writer()


class FakeSocket(object):
    """ A fake socket for testing dogstatsd client.

        Adapted from https://github.com/DataDog/datadogpy/blob/master/tests/unit/dogstatsd/test_statsd.py#L31
    """

    def __init__(self):
        self.payloads = deque()

    def send(self, payload):
        if PY3:
            assert type(payload) == bytes
        else:
            assert type(payload) == str
        self.payloads.append(payload)

    def recv(self):
        try:
            return self.payloads.popleft().decode('utf-8')
        except IndexError:
            return None

    def close(self):
        pass

    def __repr__(self):
        return str(self.payloads)
