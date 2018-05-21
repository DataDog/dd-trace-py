import opentracing


class Tracer(opentracing.Tracer):
    """"""

    __slots__ = []

    def __init__(self, scope_manager=None):
        pass

    @property
    def scope_manager(self):
        """"""
        pass

    @property
    def active_span(self):
        """"""
        pass

    def start_active_span(self, operation_name, child_of=None, references=None,
                          tags=None, start_time=None, ignore_active_span=False,
                          finish_on_close=True):
        """"""
        pass

    def start_span(self, operation_name=None, child_of=None, references=None,
                   tags=None, start_time=None, ignore_active_span=False):
        """"""
        pass

    def inject(self, span_context, format, carrier):
        """"""
        pass

    def extract(self, span_context, format, carrier):
        """"""
        pass
