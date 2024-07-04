from dataclasses import dataclass
import typing

from ddtrace.profiling import event


@dataclass(slots=True)
class StackSampleEvent(event.StackBasedEvent):
    """A sample storing executions frames for a thread."""

    # Wall clock
    wall_time_ns: int = 0
    # CPU time in nanoseconds
    cpu_time_ns: int = 0


@dataclass(slots=True)
class StackExceptionSampleEvent(event.StackBasedEvent):
    """A a sample storing raised exceptions and their stack frames."""

    exc_type: typing.Optional[str] = None
