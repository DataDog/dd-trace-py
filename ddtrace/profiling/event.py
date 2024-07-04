from collections import namedtuple
from dataclasses import dataclass
from dataclasses import field
import typing

from ddtrace._trace import span as ddspan  # noqa:F401
from ddtrace.internal import compat


_T = typing.TypeVar("_T")

DDFrame = namedtuple("DDFrame", ["file_name", "lineno", "function_name", "class_name"])
StackTraceType = typing.List[DDFrame]


@dataclass(**compat.dataclass_slots())
class Event(object):
    """An event happening at a point in time."""

    timestamp = field(default_factory=compat.time_ns)

    @property
    def name(self):
        # type: (...) -> str
        """Name of the event."""
        return self.__class__.__name__


@dataclass(**compat.dataclass_slots())
class TimedEvent(Event):
    """An event that has a duration."""

    duration = None


@dataclass(**compat.dataclass_slots())
class SampleEvent(Event):
    """An event representing a sample gathered from the system."""

    sampling_period = None


@dataclass(**compat.dataclass_slots())
class StackBasedEvent(SampleEvent):
    thread_id: typing.Optional[int] = None
    thread_name: typing.Optional[str] = None
    thread_native_id: typing.Optional[int] = None
    task_id: typing.Optional[int] = None
    task_name: typing.Optional[str] = None
    frames: typing.Optional[StackTraceType] = None
    nframes: int = 0
    local_root_span_id: typing.Optional[int] = None
    span_id: typing.Optional[int] = None
    trace_type: typing.Optional[str] = None
    trace_resource_container: typing.Optional[typing.List[str]] = None

    def set_trace_info(
        self,
        span,  # type: typing.Optional[ddspan.Span]
        endpoint_collection_enabled,  # type: bool
    ):
        # type: (...) -> None
        if span:
            self.span_id = span.span_id
            if span._local_root is not None:
                self.local_root_span_id = span._local_root.span_id
                self.trace_type = span._local_root.span_type
                if endpoint_collection_enabled:
                    self.trace_resource_container = span._local_root._resource
