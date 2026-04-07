from dataclasses import dataclass

from ddtrace._trace.events import TracingEvent
from ddtrace.internal.core.events import event_field


@dataclass
class GenericOperationEvent(TracingEvent):
    """Generic event for internal operations.

    Use for spans that don't fit a specific category (HTTP client, database, etc.)
    such as connection-level operations, greenlet calls, or framework hooks.
    """

    event_name = "internal.operation"

    span_type = ""
    span_kind = "internal"

    span_name: str = event_field()
    measured: bool = False
