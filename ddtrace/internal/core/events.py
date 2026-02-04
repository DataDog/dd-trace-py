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

    @dataclass
    class TestContextEvent(ContextEvent):
        event_name = "test.event"
        span_kind = "kind"
        component = "component"
        span_type = "type"

        foo: str
        bar: str

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

    # Create an instance and call create_event_context() to get the dict
    event = TestContextEvent(foo="toto", bar="tata")
    with core.context_with_event(event.create_event_context()):
        pass

"""

from dataclasses import MISSING
from dataclasses import dataclass
from dataclasses import field
from dataclasses import fields
from types import TracebackType
from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple

from ddtrace._trace.trace_handlers import _finish_span
from ddtrace._trace.trace_handlers import _start_span
from ddtrace.constants import SPAN_KIND
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT


def event_field(
    default: Any = MISSING,
    default_factory: Any = MISSING,
    in_context: bool = False,
) -> Any:
    if default is not MISSING and default_factory is not MISSING:
        raise ValueError("Cannot specify both default and default_factory")

    kwargs: Dict[str, Any] = {"repr": in_context}
    if default is not MISSING:
        kwargs["default"] = default
    elif default_factory is not MISSING:
        kwargs["default_factory"] = default_factory

    return field(**kwargs)


def span_context_event(cls: Any) -> Any:
    # Get annotations before dataclass processes them
    annotations = getattr(cls, "__annotations__", {})

    # For each annotated field without a default, set it to event_field()
    for name, _ in annotations.items():
        if not hasattr(cls, name):
            setattr(cls, name, event_field())

    return dataclass(cls)


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


@dataclass
class BaseContextEvent:
    event_name: str = field(init=False, repr=False)

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        # Prevent registering SpanContextEvent class
        if "event_name" not in cls.__dict__:
            return

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

    def create_event_context(self):
        """Convert this event instance into a dict that is used to create an ExecutionContext.

        If the event class is a dataclass, all fields are automatically extracted and added
        to the context dict. Additional keyword arguments can be passed via **extra.

        Every key from this dict can be accessed during Context lifetime using ctx.get_item()
        """
        return {f.name: getattr(self, f.name) for f in fields(self) if f.repr}

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
        # _on_context_started will be called in order from parent class to children classes.
        for base_cls in reversed(cls.__mro__[:-1]):
            if issubclass(base_cls, BaseContextEvent) and "_on_context_started" in base_cls.__dict__:
                base_cls._on_context_started(ctx, call_trace, **kwargs)

    @classmethod
    def _registered_context_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        # _on_context_ended will be called in order from parent class to children classes.
        for base_cls in reversed(cls.__mro__[:-1]):
            if issubclass(base_cls, BaseContextEvent) and "_on_context_ended" in base_cls.__dict__:
                base_cls._on_context_ended(ctx, exc_info)


@dataclass
class SpanContextEvent(BaseContextEvent):
    """ContextEvent is an abstraction created to constrain the arguments that can be passed
    during a call to core.context_with_data

    It can used with core.context_with_data(MyBaseEvent()).
    """

    span_name: str = field(init=False)
    span_type: str = field(init=False)
    span_kind: str = field(init=False, repr=False)
    component: str = field(init=False, repr=False)
    call_trace: bool = field(default=True, kw_only=True)
    service: Optional[str] = field(default=None, kw_only=True)
    resource: Optional[str] = field(default=None, kw_only=True)
    tags: Dict[str, str] = field(default_factory=dict, init=False)

    def create_event_context(self):
        """Convert this event instance into a dict that is used to create an ExecutionContext.

        For SpanContextEvent, this automatically includes span_type, span_name, call_trace, and tags.
        If the event class is a dataclass, all fields are automatically extracted and added
        to the context dict.

        Every key from this dict can be accessed during Context lifetime using ctx.get_item()
        """
        self.tags[COMPONENT] = self.component
        self.tags[SPAN_KIND] = self.span_kind

        return super().create_event_context()

    @classmethod
    def _registered_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
        _start_span(ctx, call_trace, **kwargs)

        # _on_context_started will be called in order from parent class to children classes.
        for base_cls in reversed(cls.__mro__[:-1]):
            if issubclass(base_cls, BaseContextEvent) and "_on_context_started" in base_cls.__dict__:
                base_cls._on_context_started(ctx, call_trace, **kwargs)

    @classmethod
    def _registered_context_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        # _on_context_ended will be called in order from parent class to children classes.
        for base_cls in reversed(cls.__mro__[:-1]):
            if issubclass(base_cls, BaseContextEvent) and "_on_context_ended" in base_cls.__dict__:
                base_cls._on_context_ended(ctx, exc_info)

        _finish_span(ctx, exc_info)
