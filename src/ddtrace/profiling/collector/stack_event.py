import typing

import attr

from ddtrace.profiling import event


@event.event_class
class StackSampleEvent(event.StackBasedEvent):
    """A sample storing executions frames for a thread."""

    # Wall clock
    wall_time_ns = attr.ib(default=0, type=int)
    # CPU time in nanoseconds
    cpu_time_ns = attr.ib(default=0, type=int)


@event.event_class
class StackExceptionSampleEvent(event.StackBasedEvent):
    """A a sample storing raised exceptions and their stack frames."""

    exc_type = attr.ib(default=None, type=typing.Optional[str])
