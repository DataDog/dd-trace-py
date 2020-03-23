from ddtrace import compat
from ddtrace.vendor import attr


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
