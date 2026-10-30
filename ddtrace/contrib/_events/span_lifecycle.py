from collections.abc import Callable
from collections.abc import Sequence
from enum import Enum
from typing import Any
from typing import ClassVar

from ddtrace.internal import core


class SpanLifecycleEvents(Enum):
    # Dispatched by the tracing SpanLifecycleSubscriber as "<value>.<context identifier>" with (ctx,),
    # so products can take part in a span's lifecycle without depending on the tracer.
    SPAN_STARTING = "span.starting"
    SPAN_STARTED = "span.started"
    SPAN_FINISHING = "span.finishing"


class SpanLifecycleListener:
    """Takes part in the lifecycle of the spans created for the contexts named in ``event_names``.

    One class covers the whole lifecycle:
    - on_starting: before the span is created, the event can still be changed
    - on_started: after the span is created
    - on_finishing: before the span is finished

    Listeners are not registered on definition: the product that owns them registers them.
    """

    event_names: ClassVar[Sequence[str]]

    @classmethod
    def register(cls) -> None:
        for event_name, phase, handler in cls._listeners():
            core.on(event_name, handler, name=f"{cls.__name__}.{phase.name}")

    @classmethod
    def unregister(cls) -> None:
        for event_name, _, handler in cls._listeners():
            core.reset_listeners(event_name, handler)

    @classmethod
    def on_starting(cls, ctx: core.ExecutionContext[Any]) -> None:
        pass

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[Any]) -> None:
        pass

    @classmethod
    def on_finishing(cls, ctx: core.ExecutionContext[Any]) -> None:
        pass

    @classmethod
    def _listeners(cls) -> list[tuple[str, SpanLifecycleEvents, Callable[[core.ExecutionContext[Any]], None]]]:
        handlers = (
            (SpanLifecycleEvents.SPAN_STARTING, cls.on_starting),
            (SpanLifecycleEvents.SPAN_STARTED, cls.on_started),
            (SpanLifecycleEvents.SPAN_FINISHING, cls.on_finishing),
        )
        return [
            (f"{phase.value}.{event_name}", phase, handler)
            for event_name in cls.event_names
            for phase, handler in handlers
        ]
