from ddtrace.opentracer.span import Span


class TestSpan(object):

    def test_init(self):
        """Very basic test for skeleton code"""
        span = Span(None, None, None)
        assert span is not None
