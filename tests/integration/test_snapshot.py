from ddtrace import Tracer
from tests import TracerTestCase, snapshot


class TestTraces(TracerTestCase):
    """
    These snapshot tests ensure that trace payloads are being sent as expected.
    """

    @snapshot()
    def test_single_trace_single_span(self):
        t = Tracer()
        s = t.trace("operation", service="my-svc")
        s.set_tag("k", "v")
        # numeric tag
        s.set_tag("num", 1234)
        s.set_metric("float_metric", 12.34)
        s.set_metric("int_metric", 4321)
        s.finish()
        t.shutdown()

    @snapshot()
    def test_multiple_traces(self):
        tracer = Tracer()
        with tracer.trace("operation1", service="my-svc") as s:
            s.set_tag("k", "v")
            s.set_tag("num", 1234)
            s.set_metric("float_metric", 12.34)
            s.set_metric("int_metric", 4321)
            tracer.trace("child").finish()

        with tracer.trace("operation2", service="my-svc") as s:
            s.set_tag("k", "v")
            s.set_tag("num", 1234)
            s.set_metric("float_metric", 12.34)
            s.set_metric("int_metric", 4321)
            tracer.trace("child").finish()
        tracer.shutdown()

    @snapshot()
    def test_filters(self):
        t = Tracer()

        class FilterMutate(object):
            def __init__(self, key, value):
                self.key = key
                self.value = value

            def process_trace(self, trace):
                for s in trace:
                    s.set_tag(self.key, self.value)
                return trace

        t.configure(
            settings={
                "FILTERS": [FilterMutate("boop", "beep")],
            }
        )

        with t.trace("root"):
            with t.trace("child"):
                pass
        t.shutdown()
