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

    @context_event
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
from typing import Any
from typing import Dict
from typing import Optional
from typing import TypeVar

from ddtrace.constants import SPAN_KIND
from ddtrace.internal.constants import COMPONENT


_CONTEXT_FIELDS_ATTR = "__dd_context_event_fields__"
_PY3_9 = sys.version_info <= (3, 10)


class Event:
    """BaseEvent is an abstraction created to constrain the arguments that can be passed
    during a call to core.dispatch()

    It can be used with core.dispatch_event(Event()).

    Every children classes should be a @dataclass
    """

    event_name: str


def context_event(cls: type[_T]) -> type[_T]:
    """Decorator that converts a class into a dataclass with automatic event_field() defaults.

    By automatically applying event_field() to fields without defaults, this decorator allows child classes
    to define required fields naturally while maintaining compatibility with parent class fields that have defaults.

    Example:
        @context_event
        class MyEvent(SpanContextEvent):
            url: str  # Automatically gets event_field() applied
    """

    annotations = cls.__dict__.get("__annotations__", {})

    # For each annotated field without a default, set it to event_field()
    for name, _ in annotations.items():
        # Only apply `event_field()` for fields that don't define a default value
        # in the class body.
        if name not in cls.__dict__:
            setattr(cls, name, event_field())

    dc_cls = dataclass(cls)
    setattr(dc_cls, _CONTEXT_FIELDS_ATTR, {f.name for f in fields(dc_cls) if f.repr})

    return dc_cls


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
    if _PY3_9:
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

    def _apply_event_context(self, out: Dict[str, Any]) -> None:
        names = getattr(self.__class__, _CONTEXT_FIELDS_ATTR, None)
        if names is None:
            # Defensive fallback in case a class wasn't decorated with @context_event.
            names = tuple(f.name for f in fields(self) if f.repr)

        d = self.__dict__
        for field_name in names:
            # PERF: `__dict__` access is faster than getattr in the hot-path.
            # Fallback to getattr for edge cases (unlikely for dataclass instances).
            try:
                out[field_name] = d[field_name]
            except KeyError:
                out[field_name] = getattr(self, field_name)

    def create_event_context(self):
        """Convert this event instance into a dict that is used to create an ExecutionContext.
        Only event_field marked with in_context=True will be passed to the ExeuctionContext
        """
        names = getattr(self.__class__, _CONTEXT_FIELDS_ATTR)
        return {field_name: getattr(self, field_name) for field_name in names}


@dataclass
class SpanContextEvent(ContextEvent):
    """SpanContextEvent is a specialization of ContextEvent. It allows to automatically start a span
    when being dispatched as well as enforcing some attributes on any SpanContextEvent
    """

    # This attributes are not instance sp ecific and should be set at Event definition
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
