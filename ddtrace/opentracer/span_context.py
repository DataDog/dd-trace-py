from opentracing import SpanContext as OpenTracingSpanContext

from ddtrace.context import Context


from ddtrace.context import Context


class SpanContext(OpenTracingSpanContext):
    """Implementation of the OpenTracing span context."""

    def __init__(self, trace_id=None, span_id=None, sampled=True,
                 sampling_priority=None, baggage=None, context=None):

        baggage = baggage or OpenTracingSpanContext.EMPTY_BAGGAGE

        if context:
            self._context = context
        else:
            self._context = Context(
                trace_id=trace_id,
                span_id=span_id,
                sampled=sampled,
                sampling_priority=sampling_priority,
            )

        self._baggage = baggage

    @property
    def baggage(self):
        return self._baggage

    def with_baggage_item(self, key, value):
        """Creates a copy of this span with a new baggage item.

        This method helps to preserve immutability of the span context.
        """

        baggage = dict(self._baggage)
        baggage[key] = value
        return SpanContext(context=self._context, baggage=baggage)
