from dataclasses import dataclass
from types import TracebackType
from typing import Optional
from typing import Tuple

from ddtrace._trace.trace_handlers.common import _finish_span
from ddtrace._trace.trace_handlers.common import _start_span
from ddtrace.internal import core


@dataclass
class BaseEvent:
    event_name: str

    _registered_events = set()

    def __post_init__(self):
        if self.event_name not in BaseEvent._registered_events:
            core.on(self.event_name, self.on_event)
            BaseEvent._registered_events.add(self.event_name)

    def on_event(self, *args, **kwargs) -> None:
        pass


@dataclass
class FinishEvent:
    event_name: str
    ctx: core.ExecutionContext
    error: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]]

    _registered_events = set()

    def __post_init__(self):
        """Automatically register event handler when instance is created."""
        if self.event_name not in FinishEvent._registered_events:
            core.on(self.event_name, _generic_finish_event_handler)
            FinishEvent._registered_events.add(self.event_name)

    def on_finish(self) -> None:
        """Override this method in subclasses to add custom logic before span finishes."""
        pass


def _generic_finish_event_handler(event):
    event.on_finish()
    _finish_span(event.ctx, event.error)


@dataclass
class ContextEvent(BaseEvent):
    # Track registered event names to avoid duplicate registrations
    _registered_events = set()

    def __post_init__(self):
        """Automatically register event handlers when instance is created."""
        if self.event_name not in ContextEvent._registered_events:
            register_context_event_handlers(self.event_name)
            ContextEvent._registered_events.add(self.event_name)

    def on_context_started(self, ctx: core.ExecutionContext) -> None:
        """Override this method in subclasses to add custom logic when context starts."""
        pass

    def on_context_ended(
        self,
        ctx: core.ExecutionContext,
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        """Override this method in subclasses to add custom logic before span finishes."""
        pass


def _generic_context_started_handler(ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
    _start_span(ctx, call_trace, **kwargs)

    on_context_started = ctx.get_item("_event_on_context_started")
    if on_context_started:
        on_context_started(ctx)


def _generic_context_ended_handler(
    ctx: core.ExecutionContext, exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]]
) -> None:
    on_context_ended = ctx.get_item("_event_on_context_ended")
    if on_context_ended:
        on_context_ended(ctx, exc_info)

    if ctx.get_item("_context_end") is not False:
        _finish_span(ctx, exc_info)


def register_context_event_handlers(event_name: str) -> None:
    core.on(f"context.started.{event_name}", _generic_context_started_handler)
    core.on(f"context.ended.{event_name}", _generic_context_ended_handler)
