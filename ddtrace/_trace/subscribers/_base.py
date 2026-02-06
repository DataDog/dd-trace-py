from types import TracebackType
from typing import Optional
from typing import Tuple

from ddtrace._trace.trace_handlers import _finish_span
from ddtrace._trace.trace_handlers import _start_span
from ddtrace.internal import core
from ddtrace.internal.core.subscriber import BaseSubscriber


class SpanTracingSubscriber(BaseSubscriber):
    """Subscriber that creates a span on start and finishes it on end.

    Subclasses override on_started/on_ended for type-specific logic.
    Span lifecycle is handled here — subclasses never call _start_span/_finish_span.
    """

    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
        _start_span(ctx, call_trace, **kwargs)
        for handler in cls._started_handlers:
            handler(ctx, call_trace, **kwargs)

    @classmethod
    def _on_context_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        try:
            for handler in cls._ended_handlers:
                handler(ctx, exc_info)
        finally:
            _finish_span(ctx, exc_info)
