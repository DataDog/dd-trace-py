import typing

import attr

from ddtrace import span as ddspan
from ddtrace.internal import compat


def event_class(klass):
    return attr.s(slots=True)(klass)


@event_class
class Event(object):
    """An event happening at a point in time."""

    timestamp = attr.ib(factory=compat.time_ns)

    @property
    def name(self):
        """Name of the event."""
        return self.__class__.__name__


@event_class
class TimedEvent(Event):
    """An event that has a duration."""

    duration = attr.ib(default=None)


@event_class
class SampleEvent(Event):
    """An event representing a sample gathered from the system."""

    sampling_period = attr.ib(default=None)


@event_class
class StackBasedEvent(SampleEvent):
    thread_id = attr.ib(default=None)
    thread_name = attr.ib(default=None)
    thread_native_id = attr.ib(default=None)
    task_id = attr.ib(default=None)
    task_name = attr.ib(default=None)
    frames = attr.ib(default=None)
    nframes = attr.ib(default=None)
    trace_id = attr.ib(default=None, type=typing.Optional[int])
    span_id = attr.ib(default=None, type=typing.Optional[int])
    trace_type = attr.ib(default=None, type=typing.Optional[str])
    trace_resource = attr.ib(default=None, type=typing.Optional[str])

    def set_trace_info(
        self,
        span,  # type: typing.Optional[ddspan.Span]
    ):
        # type: (...) -> None
        if span is not None:
            self.trace_id = span.trace_id
            self.span_id = span.span_id
            if span._local_root is not None:
                self.trace_resource = span._local_root.resource
                self.trace_type = span._local_root.span_type
            span._on_finish_callbacks.append(self._update_trace_resource)

    def _update_trace_resource(
        self,
        span,  # type: ddspan.Span
    ):
        # type: (...) -> None
        if span._local_root is not None:
            self.trace_resource = span._local_root.resource
