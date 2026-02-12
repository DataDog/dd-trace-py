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

from dataclasses import dataclass
from dataclasses import field
import sys
from typing import Any
from typing import ClassVar
from typing import Dict
from typing import Optional


_PY3_9 = sys.version_info <= (3, 10)


def event_field() -> Any:
    """Creates a dataclass field with special handling to ensure retro compatibility
       as python 3.9 does not support kw_only which is required by SpanContextEvent to
       allow a child class to have attributes without value.

    Args:
        default: Default value for the field
        default_factory: Factory function to generate default values
        in_context: Whether this field should be included in the ExecutionContext dict
    """
    kwargs: Dict[str, Any] = {}
    if _PY3_9:
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


@dataclass
class SpanContextEvent(Event):
    """SpanContextEvent is a specialization of ContextEvent. It enforces minimal attributes
    on any SpanContextEvent
    """

    # This attributes are most of the time not instance specific and should be set at Event definition
    span_name: ClassVar[str]
    span_type: ClassVar[str]
    span_kind: ClassVar[str]
    component: ClassVar[str]

    tags: Dict[str, str] = field(default_factory=dict, init=False)
    _end_span: bool = field(default=True, init=False)

    call_trace: bool = True
    service: Optional[str] = None
    distributed_context: Optional[Any] = None
    resource: Optional[str] = None
    measured: bool = False

    def set_component(self, component):
        self.__class__.component = component
