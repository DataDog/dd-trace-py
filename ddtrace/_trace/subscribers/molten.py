from typing import Optional

from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.contrib._events.molten import MoltenRouterMatchEvent
from ddtrace.contrib._events.molten import MoltenTraceEvent
from ddtrace.contrib._events.web_framework import WebRequestEvent
from ddtrace.ext.http import ROUTE
from ddtrace.internal import core
from ddtrace.internal.core.subscriber import Subscriber


MOLTEN_ROUTE = "molten.route"


class MoltenTracingSubscriber(TracingSubscriber):
    event_names = (MoltenTraceEvent.event_name,)


class MoltenRouterMatchSubscriber(Subscriber):
    event_names = (MoltenRouterMatchEvent.event_name,)

    @classmethod
    def on_event(cls, event_instance: MoltenRouterMatchEvent):
        event: Optional[WebRequestEvent] = core.get_event()
        if event:
            event.set_resource = False

        span = core.get_span()
        if span:
            route = event_instance.route
            span.resource = f"{route.method} {route.template}"
            if not span.get_tag(MOLTEN_ROUTE):
                span._set_tag_str(MOLTEN_ROUTE, route.name)
            if not span.get_tag(ROUTE):
                span._set_tag_str(ROUTE, route.template)
