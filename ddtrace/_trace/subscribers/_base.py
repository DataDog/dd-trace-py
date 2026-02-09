from types import TracebackType
from typing import Optional
from typing import Tuple

from ddtrace._trace.trace_handlers import _finish_span
from ddtrace._trace.trace_handlers import _start_span
from ddtrace.internal import core
from ddtrace.internal.core.subscriber import BaseContextSubscriber


class SpanTracingSubscriber(BaseContextSubscriber):
    """Subscriber that automatically manages span lifecycle for SpanContextEvent.

    This base class handles span creation and finishing, so subclasses only need to
    override on_started/on_ended for their specific logic.

    Example:
        class MySpanSubscriber(SpanTracingSubscriber):
            event_name = "my.span"

            @classmethod
            def on_started(cls, ctx, call_trace=True, **kwargs):
                ctx.span.set_tag("custom.tag", "value")

            @classmethod
            def on_ended(cls, ctx, exc_info):
                if exc_info[1]:
                    ctx.span.set_tag("error", True)

    Attributes:
        _end_span: If False, span won't be finished automatically (defaults to True)
    """

    _end_span = True

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
            if cls._end_span:
                _finish_span(ctx, exc_info)
