from ddtrace.opentracer.span_context import SpanContext


class TestSpanContext(object):

    def test_init(self):
        """Make sure span context creation is fine."""
        span_ctx = SpanContext()
        assert span_ctx

    def test_baggage(self):
        """Ensure baggage passed is the resulting baggage of the span context."""
        baggage = {
            'some': 'stuff',
        }

        span_ctx = SpanContext(baggage=baggage)

        assert span_ctx.baggage is baggage
