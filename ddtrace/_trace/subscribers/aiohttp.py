from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.contrib._events.aiohttp import AIOHttpConnectEvent
from ddtrace.contrib._events.aiohttp import AIOHttpCreateConnectEvent


class AIOHttpConnectSubscriber(TracingSubscriber):
    event_names = (AIOHttpConnectEvent.event_name,)


class AIOHttpCreateConnectSubscriber(TracingSubscriber):
    event_names = (AIOHttpCreateConnectEvent.event_name,)
