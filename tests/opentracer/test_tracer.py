from ddtrace.opentracer import Tracer


class TestTracer(object):

    def test_init(self):
        """Very basic test for skeleton code"""
        tracer = Tracer()
        assert tracer is not None
