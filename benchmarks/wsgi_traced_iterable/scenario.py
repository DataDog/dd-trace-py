import bm

from ddtrace.contrib.internal.wsgi import wsgi as _wsgi  # noqa:F401


class _Span:
    __slots__ = ("finished",)

    def __init__(self):
        self.finished = False

    def finish(self):
        self.finished = True


class WSGITracedIterableScenario(bm.Scenario):
    chunks: int

    def run(self):
        from ddtrace._trace.trace_handlers import _TracedIterable

        response = (b"chunk",) * self.chunks
        span = _Span()
        parent_span = _Span()

        def traced_iterable(loops):
            for _ in range(loops):
                for _ in _TracedIterable(response, span, parent_span):
                    pass

        yield traced_iterable
