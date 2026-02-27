"""
This files implements typed events models for ``core.dispatch_event`` and ``core.context_with_event``
More specifically, this module defines dataclass-based event objects that are used in two ways:

1. Dispatch-only events with ``core.dispatch_event(event_instance)``.
   A listener registered with ``core.on(<event_name>, callback)`` receives the event
   instance directly.

   Example::

       @dataclass
       class MyEvent(Event):
           event_name = "my.event"
           data: str

       core.dispatch_event(MyEvent(data="hello"))

2. Context lifecycle events with ``core.context_with_event(event_instance)``.
   The context manager emits ``context.started.<event_name>`` and
   ``context.ended.<event_name>`` events, and listeners can access the original event
   through ``ctx.event``.

   Example::

       @dataclass
       class MyContextEvent(Event):
           event_name = "my.context"
           user_id: str = event_field()
           not_in_context: InitVar[str] = event_field()

           def __post_init__(self, not_in_context):
               doSomething()

       def on_started(ctx):
           assert ctx.event.user_id == "123"

       core.on("context.started.my.context", on_started)
       with core.context_with_event(MyContextEvent(user_id="123")):
           pass

For event classes used with ``core.context_with_event(...)``, use ``event_field()``
for every dataclass attribute:

1. Persistent event attributes (available on ``ctx.event``) should be declared as:
   ``my_var: MyType = event_field()``.

2. Init-only attributes (consumed during construction but not stored on the instance)
   should be declared with ``InitVar``:
   ``my_var: InitVar[MyType] = event_field()``.
   Those values are available in ``__post_init__``.

Defaults must be defined in ``event_field(...)`` (for example ``default=...`` or
``default_factory=...``), not as plain dataclass assignment without ``event_field``.
"""

from dataclasses import MISSING
from dataclasses import dataclass
from dataclasses import field
import sys
from typing import Any
from typing import ClassVar
from typing import TypeVar


EventType = TypeVar("EventType", bound="Event")

_PY3_9 = sys.version_info < (3, 10)


def event_field(default: Any = MISSING, default_factory: Any = MISSING) -> Any:
    """Creates a dataclass field with special handling to ensure retro compatibility
    as python 3.9 does not support kw_only which is required by TracingEvent to
    allow a child class to have attributes without a default value.
    """
    kwargs: dict[str, Any] = {}
    if default is not MISSING:
        kwargs["default"] = default
    if default_factory is not MISSING:
        kwargs["default_factory"] = default_factory

    if _PY3_9:
        # Python 3.9 does not support dataclass kw_only fields.
        # Keep default=None fallback only when no explicit default/default_factory is provided.
        if default is MISSING and default_factory is MISSING:
            kwargs["default"] = None
    else:
        kwargs["kw_only"] = True

    return field(**kwargs)


@dataclass
class Event:
    """BaseEvent is an abstraction created to constrain the arguments that can be passed
    during a call to core.dispatch() or core.context_with_data()

    It can be used with core.dispatch_event(Event()).

    Every child class should be a @dataclass
    """

    event_name: ClassVar[str]
