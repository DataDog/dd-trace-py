from ddtrace._trace.events import TracingEvent
from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.contrib._events.temporalio import TemporalEvents


class TemporalTracingSubscriber(TracingSubscriber[TracingEvent]):
    event_names = tuple(event.value for event in TemporalEvents)
