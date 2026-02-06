from ddtrace.internal.core.subscriber import BaseSubscriber
from ddtrace._trace.trace_handlers import _start_span
from ddtrace._trace.trace_handlers import _finish_span

class SpanTracingSubscriber(BaseSubscriber):
    """Subscriber that creates a span on start and finishes it on end.

    Subclasses override on_started/on_ended for type-specific logic.
    Span lifecycle is handled here — subclasses never call _start_span/_finish_span.
    """

    @classmethod
    def _on_context_started(cls, ctx, call_trace=True, **kwargs):
        _start_span(ctx, call_trace, **kwargs)
        cls.on_started(ctx, call_trace, **kwargs)

    @classmethod
    def _on_context_ended(cls, ctx, exc_info):
        try:
            cls.on_ended(ctx, exc_info)
        finally:
            _finish_span(ctx, exc_info)