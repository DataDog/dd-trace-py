from dataclasses import dataclass

from ddtrace.internal import core
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.core.events import Event


@dataclass
class ContextExceptionEvent(Event):
    """Synchronous exception notification; consumers must not retain its traceback."""

    event_name = "context.exception"

    context: core.ExecutionContext[Event]
    exc_info: ExcInfoType
