from types import TracebackType
from typing import Optional
from typing import Tuple

from ddtrace._trace.trace_handlers import _finish_span
from ddtrace._trace.trace_handlers import _start_span
from ddtrace.internal import core


class BaseEvent:
    event_name: str

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        if "on_event" in cls.__dict__:
            core.on(cls.event_name, cls.on_event)

    @classmethod
    def on_event(cls, *args, **kwargs) -> None:
        pass


class ContextEvent:
    event_name: str
    span_kind: Optional[str] = None
    component: Optional[str] = None
    span_type: Optional[str] = None
    call_trace: bool = False

    _start_span: bool = True
    _end_span: bool = True

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        if cls._start_span:
            required_attrs = [
                attr
                for attr in ContextEvent.__dict__
                if not attr.startswith("_") and not callable(getattr(ContextEvent, attr, None))
            ]
            missing_attrs = [attr for attr in required_attrs if not hasattr(cls, attr)]
            if missing_attrs:
                raise TypeError(f"{cls.__name__} must define class attributes: {', '.join(missing_attrs)}")

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
    def get_default_tags(cls):
        """
        Returns the default tags for this event type.
        Subclasses should override this method to provide their own default tags.
        """
        return {}

    @classmethod
    def get_tags(cls, additional_tags=None):
        """Merges default tags with additional instance-specific tags."""
        tags = cls.get_default_tags()
        if additional_tags:
            tags.update(additional_tags)
        return tags

    @classmethod
    def create(cls, *, span_name=None, tags=None, **extra):
        """Build the event data dictionary with standard structure."""
        return {
            "event_name": cls.event_name,
            "span_type": cls.span_type,
            "span_name": span_name,
            "call_trace": cls.call_trace,
            "tags": cls.get_tags(tags),
            **extra,
        }

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
