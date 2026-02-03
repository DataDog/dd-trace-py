"""
This file implements the Events API. This is an abstraction above the Core API, i.e
events are directly using the Core API.

The main goal of the Events API is to group behavior for similar integrations.
For instance, all HTTP clients should use the same Events.

Additionnaly the Events API is:
- Enforcing args that can be passed when dispatching events
- Decongesting trace_handlers.py by grouping events and handlers definition

This example shows how ``core.dispatch()`` can be replaced by `core.dispatch_event()`::

    @dataclass
    class TestEvent(BaseEvent):
        event_name = "test.event"
        foo: str

        @classmethod
        def on_event(cls, event_instance, *args):
            print(event_instance.foo)

        core.dispatch_event(TestEvent(foo="toto"), "additional", "args")

This example shows how ``core.context_with_data()`` can be replaced by `core.context_with_event()`::

    class TestContextEvent(ContextEvent):
        event_name = "test.event"
        span_kind = "kind"
        component = "component"
        span_type = "type"

        def __new__(cls, foo, bar):
            return cls.create(span_name="test", foo=foo, bar=bar)

        @classmethod
        def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            span = ctx.span
            span._set_tag_str("foo", ctx.get_item("foo"))

        @classmethod
        def _on_context_ended(
            cls,
            ctx: core.ExecutionContext,
            exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
        ) -> None:
            span = ctx.span
            span._set_tag_str("bar", ctx.get_item("bar"))

    with core.context_with_event(TestContextEvent(foo="toto", bar="tata")):
        pass

"""

from types import TracebackType
from typing import Optional
from typing import Tuple

from ddtrace._trace.trace_handlers import _finish_span
from ddtrace._trace.trace_handlers import _start_span
from ddtrace.internal import core


class BaseEvent:
    """BaseEvent is an abstraction created to constrain the arguments that can be passed
    during a call to core.dispatch()

    It can be used with core.dispatch_event(MyBaseEvent()).

    Every children classes should be a @dataclass
    """

    event_name: str

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        # Automatically register the event handler
        if "on_event" in cls.__dict__:
            core.on(cls.event_name, cls.on_event)

    @classmethod
    def on_event(cls, event_instance, *additional_args) -> None:
        """To access args enforced by a BaseEvent, such as BaseEvent(foo="bar"),
        event_instance.foo must be used

        An arbitrary number of args through additional_args can be accepted using
        dispatch_event(BaseEvent(), "foo", 1) but it should be used only if necessary
        """
        pass


class ContextEvent:
    """ContextEvent is an abstraction created to constrain the arguments that can be passed
    during a call to core.context_with_data

    It can used with core.context_with_data(MyBaseEvent()).
    """

    event_name: str
    # if a ContextEvent is used to create a span the below fields are required
    span_kind: Optional[str] = None
    component: Optional[str] = None
    span_type: Optional[str] = None
    call_trace: bool = False

    _start_span: bool = True
    _end_span: bool = True

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        # Enforce that attributes have a value
        # Note you can assign values during __new__ call
        if cls._start_span:
            required_attrs = [
                attr
                for attr in ContextEvent.__dict__
                if not attr.startswith("_") and not callable(getattr(ContextEvent, attr, None))
            ]
            missing_attrs = [attr for attr in required_attrs if not hasattr(cls, attr)]
            if missing_attrs:
                raise TypeError(f"{cls.__name__} must define class attributes: {', '.join(missing_attrs)}")

        # Automatically register the event handlers
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
        Subclasses can override this method to provide their own default tags.
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
        """To prevent useless allocation, ContextEvent is returning a dict that is used
        to create a ContextClass

        Every keys from this dict can be access during Context lifetime using ctx.get_item()
        """
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
        """Should be overrided by the child class only if the event needs to do something else than starting
        a span
        """
        pass

    @classmethod
    def _on_context_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        """Should be overrided by the child class only if the event needs to do something else than finishing
        a span
        """
        pass

    @classmethod
    def _registered_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
        if cls._start_span:
            _start_span(ctx, call_trace, **kwargs)

        # _on_context_started will be called in order from parent class to children classes.
        for base_cls in reversed(cls.__mro__[:-1]):
            if issubclass(base_cls, ContextEvent) and "_on_context_started" in base_cls.__dict__:
                base_cls._on_context_started(ctx, call_trace, **kwargs)

    @classmethod
    def _registered_context_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        # _on_context_ended will be called in order from parent class to children classes.
        for base_cls in reversed(cls.__mro__[:-1]):
            if issubclass(base_cls, ContextEvent) and "_on_context_ended" in base_cls.__dict__:
                base_cls._on_context_ended(ctx, exc_info)

        if cls._end_span:
            _finish_span(ctx, exc_info)
