"""
This file implements the Events API. This is an abstraction above the Core API that enforces
type-safe arguments when dispatching events.

The Events API provides three main types of events:

1. **Event**: Base class for simple events dispatched using ``core.dispatch_event(Event())``.
   Event are replacing ``core.dispatch()`` calls.

   Classes inheriting from Event class must be dataclass

   Example::

       @dataclass
       class MyEvent(Event):
           event_name = "my.event"
           data: str

       core.dispatch_event(MyEvent(data="hello"))

2. **ContextEvent**: Base class for events dispatched using ``core.context_with_event())``.
   ContextEvent are replacing ``core.context_with_data()`` calls.

   Classes inheriting from ContextEvent class must be annotated with ``@context_event``.
   The decorator automatically converts all fields to ``event_field()`` (not stored in context by default).
   Use ``event_field(in_context=True)`` to store fields in ExecutionContext for subscriber access.

   Example::

       @context_event
       class MyContextEvent(ContextEvent):
           event_name = "my.context"
           url: str  # Automatically event_field(), not in context
           user_id: str = event_field(in_context=True)  # Stored in context

       with core.context_with_event(MyContextEvent(url="/api", user_id="123")):
           # Context is active, subscribers can access user_id via ctx.get_item("user_id")
           pass

3. **SpanContextEvent**: Specialized ContextEvent that automatically creates and manages a span.
   Requires ``span_name``, ``span_type``, ``span_kind``, and ``component`` attributes.
   These attributes can be set at runtime using ``__post_init__()``.
   Like ContextEvent, use ``@context_event`` decorator and ``event_field(in_context=True)`` for context storage.

   Example::

       @context_event
       class MySpanEvent(SpanContextEvent):
           event_name = "my.span"
           span_name = "my_operation"
           span_type = "custom"
           span_kind = "internal"
           component = "my_component"

           url: str  # Automatically event_field(), not in context
           user_id: str = event_field(in_context=True)  # Stored in context

       with core.context_with_event(MySpanEvent(url="/api", user_id="123")):
           # Span is automatically created and finished
           pass

For handling these events, see ``ddtrace.internal.core.subscriber`` for the subscriber pattern.
"""

from dataclasses import MISSING
from dataclasses import dataclass
from dataclasses import field
from dataclasses import fields
import sys
from typing import Any
from typing import Dict
from typing import Optional

from ddtrace.constants import SPAN_KIND
from ddtrace.internal.constants import COMPONENT


_CONTEXT_FIELDS_ATTR = "__dd_context_event_fields__"
_PY3_9 = sys.version_info <= (3, 10)


def event_field(
    default: Any = MISSING,
    default_factory: Any = MISSING,
    in_context: bool = False,
) -> Any:
    """Creates a dataclass field with special handling to ensure retro compatibility
       as python 3.9 does not support kw_only which is required by SpanContextEvent to
       allow a child class to have attributes without value.

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
class Event:
    """BaseEvent is an abstraction created to constrain the arguments that can be passed
    during a call to core.dispatch()

    It can be used with core.dispatch_event(Event()).

    Every children classes should be a @dataclass
    """

    event_name: str = field(init=False, repr=False)


def context_event(cls: Any) -> Any:
    """Decorator that converts a class into a dataclass and apply event_field to field without default value
    for compatibility purpose.

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


@dataclass
class ContextEvent(Event):
    """ContextEvent is an abstraction created to constrain the arguments that can be passed
    during a call to core.context_with_data()

    It can be used with core.context_with_event(ContentEvent()).

    Every children classes should be a annoted with @context_event
    """

    def create_event_context(self):
        """Convert this event instance into a dict that is used to create an ExecutionContext.
        Only event_field marked with in_context=True will be passed to the ExeuctionContext
        """
        names = getattr(self.__class__, _CONTEXT_FIELDS_ATTR)
        return {field_name: getattr(self, field_name) for field_name in names}


@dataclass
class SpanContextEvent(ContextEvent):
    """SpanContextEvent is a specialization of ContextEvent. It enforces minimal attributes
    on any SpanContextEvent
    """

    # This attributes are most of the time not instance specific and should be set at Event definition
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
