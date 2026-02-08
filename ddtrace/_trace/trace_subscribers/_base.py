from types import TracebackType
from typing import Optional
from typing import Tuple

from ddtrace._trace.trace_handlers import _finish_span
from ddtrace._trace.trace_handlers import _start_span
from ddtrace.internal import core


class TracingSubscriber:
    """Base class for tracing event subscribers.

    Subclasses that define ``event_name`` auto-register on
    context.started.{event_name} and context.ended.{event_name}.
    """

    event_name: str

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if "event_name" not in cls.__dict__:
            return
        core.on(
            f"context.started.{cls.event_name}",
            cls._on_context_started,
            name=f"{cls.__name__}.started",
        )
        core.on(
            f"context.ended.{cls.event_name}",
            cls._on_context_ended,
            name=f"{cls.__name__}.ended",
        )

    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
        cls.on_started(ctx, call_trace, **kwargs)

    @classmethod
    def _on_context_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        cls.on_ended(ctx, exc_info)

    @classmethod
    def on_started(cls, ctx, call_trace=True, **kwargs):
        pass

    @classmethod
    def on_ended(cls, ctx, exc_info):
        pass


class SpanTracingSubscriber(TracingSubscriber):
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
