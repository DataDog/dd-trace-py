"""
This file implements the Events API. This is an abstraction above the Core API, i.e
events are directly using the Core API.

The main goal of the Events API is to enforcing arguments that can be passed when dispatching events.

Events API should also allow better correlation between the dispatch and the handlers as they are
grouped in the same class.

This example shows how ``core.dispatch()`` can be replaced by `core.dispatch_event()`::

    @dataclass
    class TestEvent(Event):
        event_name = "test.event"
        foo: str

        @classmethod
        def on_event(cls, event_instance):
            print(event_instance.foo)

    def additional_handler(event_instance):
        print(f"Extended: {event_instance.foo}")
    TestEvent.extend_event(additional_handler)

    core.dispatch_event(TestEvent(foo="toto"))

This example shows how ``core.context_with_data()`` can be replaced by `core.context_with_event()`::

    @span_context
    class TestContextEvent(SpanContextEvent):
        # SpanContextEvent requires this attributes
        event_name = "test.event"
        span_kind = "kind"
        component = "component"
        span_type = "type"

        foo: str # automatically converted as an event_field by @context_event
        # but will not be stored in context

        bar: str = event_field(in_context=True)

        @classmethod
        def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            # doSomething

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

from dataclasses import MISSING
from dataclasses import dataclass
from dataclasses import field
from dataclasses import fields
import sys
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


class Event:
    """BaseEvent is an abstraction created to constrain the arguments that can be passed
    during a call to core.dispatch()

    It can be used with core.dispatch_event(Event()).

    Every children classes should be a @dataclass
    """

    event_name: str

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        # Automatically register the event handler
        if "on_event" in cls.__dict__:
            core.on(cls.event_name, cls.on_event)

    @classmethod
    def on_event(cls, event_instance) -> None:
        """To access args enforced by a BaseEvent, such as BaseEvent(foo="bar"),
        event_instance.foo must be used
        """
        pass

    @classmethod
    def extend_event(cls, handler) -> None:
        core.on(cls.event_name, handler)


def context_event(cls: Any) -> Any:
    """Decorator that converts a class into a dataclass with automatic event_field() defaults.

    By automatically applying event_field() to fields without defaults, this decorator allows child classes
    to define required fields naturally while maintaining compatibility with parent class fields that have defaults.

    Example:
        @context_event
        class MyEvent(SpanContextEvent):
            url: str  # Automatically gets event_field() applied
    """
    annotations = getattr(cls, "__annotations__", {})

    # For each annotated field without a default, set it to event_field()
    for name, _ in annotations.items():
        if not hasattr(cls, name):
            setattr(cls, name, event_field())

    return dataclass(cls)


def event_field(
    default: Any = MISSING,
    default_factory: Any = MISSING,
    in_context: bool = False,
) -> Any:
    """Creates a dataclass field with special handling for event context data and Python version compatibility.
       Event fields ensure retro compatibility as python 3.9 does not support kw_only which is
       required by SpanContextEvent to allow a child class to have attributes without value.

    Args:
        default: Default value for the field
        default_factory: Factory function to generate default values
        in_context: Whether this field should be included in the ExecutionContext dict
    """
    if default is not MISSING and default_factory is not MISSING:
        raise ValueError("Cannot specify both default and default_factory")

    kwargs: Dict[str, Any] = {"repr": in_context}
    if default is not MISSING:
        kwargs["default"] = default
    elif default_factory is not MISSING:
        kwargs["default_factory"] = default_factory

    # Python 3.9: Give fields without defaults a None default to work around
    # field ordering constraints with inheritance
    if sys.version_info < (3, 10):
        if default is MISSING and default_factory is MISSING:
            kwargs["default"] = None
    else:
        kwargs["kw_only"] = True

    return field(**kwargs)


@dataclass
class ContextEvent:
    """ContextEvent is an abstraction created to constrain the arguments that can be passed
    during a call to core.context_with_data()

    It can be used with core.context_with_event(ContentEvent()).

    Every children classes should be a annoted with @context_event
    """

    event_name: str = field(init=False, repr=False)

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        # We want to register only the hooks from children
        # classes of ContextEvent
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
        Only event_field marked with in_context=True will be passed to the ExeuctionContext
        """
        return {f.name: getattr(self, f.name) for f in fields(self) if f.repr}

    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
        """Should be overrided by the child class"""
        pass

    @classmethod
    def _on_context_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        """Should be overrided by the child class"""
        pass

    @classmethod
    def _registered_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
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


@dataclass
class SpanContextEvent(ContextEvent):
    """SpanContextEvent is a specialization of ContextEvent. It allows to automatically start a span
    when being dispatched as well as enforcing some attributes on any SpanContextEvent
    """

    # This attributes are not instance specific and should be set at Event definition
    span_name: str = field(init=False)
    span_type: str = field(init=False)
    span_kind: str = field(init=False, repr=False)
    component: str = field(init=False, repr=False)
    tags: Dict[str, str] = field(default_factory=dict, init=False)
    _end_span: bool = field(default=True, init=False, repr=False)

    call_trace: bool = event_field(default=True, in_context=True)
    service: Optional[str] = event_field(default=None, in_context=True)
    distributed_context: Optional[Any] = event_field(default=None, in_context=True)
    resource: Optional[str] = event_field(default=None, in_context=True)

    def create_event_context(self):
        self.tags[COMPONENT] = self.component
        self.tags[SPAN_KIND] = self.span_kind

        return super().create_event_context()

    @classmethod
    def _registered_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
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

        # For async package, you might not want to stop the span when context is ending
        if cls._end_span:
            _finish_span(ctx, exc_info)
