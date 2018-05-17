from opentracing import SpanContext as OpenTracingSpanContext


class SpanContext(OpenTracingSpanContext):
    """"""

    __slots__ = []

    def __init__(self, trace_id, span_id, parent_id, flags, baggage=None):
        pass

    @property
    def baggage(self):
        """"""
        pass
