from opentracing import SpanContext as OpenTracingSpanContext

from ddtrace.context import Context


class SpanContext(OpenTracingSpanContext):
    """Implementation of the opentracing span context."""

    __slots__ = ['_context', '_baggage']

    def __init__(self, trace_id=None, span_id=None, sampled=True,
                 sampling_priority=None, baggage=None, context=None):
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

    def _get_dd_context(self):
        """Return the Datadog context."""
        return self._context
