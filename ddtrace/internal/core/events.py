from types import TracebackType
from typing import Optional
from typing import Tuple

from ddtrace._trace.trace_handlers import _finish_span
from ddtrace._trace.trace_handlers import _start_span
from ddtrace.internal import core


class BaseEvent:
    event_name: str

    def __init__(self):
        if self.on_event.__func__ is not BaseEvent.on_event:
            core.on(self.event_name, self.on_event)

    def on_event(self, *args, **kwargs) -> None:
        pass


class ContextEvent:
    event_name: str
    _start_span: bool = True
    _end_span: bool = True

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        core.on(
            f"context.started.{cls.event_name}",
            cls._registered_context_started,
            name=f"{cls.__name__}_started",
        )
        core.on(
            f"context.ended.{cls.event_name}",
            cls._registered_context_ended,
            name=f"{cls.__name__}_ended",
        )

    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
        pass

    @classmethod
    def _on_context_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        pass

    @classmethod
    def _registered_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
        if cls._start_span:
            _start_span(ctx, call_trace, **kwargs)

        for base_cls in reversed(cls.__mro__[:-1]):
            if issubclass(base_cls, ContextEvent) and "_on_context_started" in base_cls.__dict__:
                base_cls._on_context_started(ctx, call_trace, **kwargs)

    @classmethod
    def _registered_context_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        for base_cls in reversed(cls.__mro__[:-1]):
            if issubclass(base_cls, ContextEvent) and "_on_context_ended" in base_cls.__dict__:
                base_cls._on_context_ended(ctx, exc_info)

        if cls._end_span:
            _finish_span(ctx, exc_info)
