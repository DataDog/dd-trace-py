from opentracing import Span as OpenTracingSpan


class Span(OpenTracingSpan):
    """Datadog implementation of :class:`opentracing.Span`"""

    def __init__(self, context, tracer, operation_name, tags=None,
                 start_time=None):
        pass

    def finish(self, finish_time=None):
        """"""
        pass

    def get_baggage_item(self, key_values, timestamp=None):
        """"""
        pass

    def set_operation_name(self, operation_name):
        """Set the operation name."""
        pass

    def log_kv(self, key_values, timestamp=None):
        """"""
        pass

    @property
    def context(self):
        """"""
        pass

    @property
    def tracer(self):
        """"""
        pass

    def set_tag(self, key, value):
        """"""
        pass
