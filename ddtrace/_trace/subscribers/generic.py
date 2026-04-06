from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.contrib._events.generic import GenericOperationEvent


class GenericOperationSubscriber(TracingSubscriber):
    """Subscriber for internal operation spans.

    No custom logic — TracingSubscriber base handles
    span creation and finishing.
    """

    event_names = (GenericOperationEvent.event_name,)
