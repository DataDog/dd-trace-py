from types import TracebackType
from typing import Optional
from typing import Tuple

from ddtrace.internal import core


class BaseSubscriber:
    """Base class for event subscribers.

    Subclasses that define ``event_name`` auto-register on
    context.started.{event_name} and context.ended.{event_name}.
    """

    event_name: str
    _started_handlers: tuple = ()
    _ended_handlers: tuple = ()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        cls._started_handlers = tuple(
            base_cls.on_started
            for base_cls in reversed(cls.__mro__[:-1])
            if issubclass(base_cls, BaseSubscriber)
            and "on_started" in base_cls.__dict__
            and base_cls is not BaseSubscriber
        )
        cls._ended_handlers = tuple(
            base_cls.on_ended
            for base_cls in reversed(cls.__mro__[:-1])
            if issubclass(base_cls, BaseSubscriber)
            and "on_ended" in base_cls.__dict__
            and base_cls is not BaseSubscriber
        )

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
    def on_started(cls, ctx, call_trace=True, **kwargs):
        pass

    @classmethod
    def on_ended(cls, ctx, exc_info):
        pass

    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
        for handler in cls._started_handlers:
            handler(ctx, call_trace, **kwargs)

    @classmethod
    def _on_context_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        # _on_context_ended will be called in order from parent class to children classes.
        for handler in cls._ended_handlers:
            handler(ctx, exc_info)
