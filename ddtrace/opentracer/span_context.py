from opentracing import SpanContext as OpenTracingSpanContext

from ddtrace.context import Context


class SpanContext(OpenTracingSpanContext):
    """
    TODO: the span context should be immutable
    """

    __slots__ = ['_context']

    def __init__(self, trace_id=None, span_id=None, sampled=True,
                 sampling_priority=None, baggage=None):
        self._context = Context(
            trace_id=trace_id,
            span_id=span_id,
            sampled=sampled,
            sampling_priority=sampling_priority,
        )

    @property
    def baggage(self):
        """"""
        pass

    def __getattr__(self, name):
        """Pass through attrs that don't exist on here to _context."""
        return object.__getattribute__(self._context, name)
