from dataclasses import dataclass

from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.internal import core
from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import event_field


@dataclass
class MoltenRouteEvent(Event):
    event_name = "molten.router.match"

    request_context: core.ExecutionContext[WebFrameworkRequestEvent] = event_field()
    resource: str = event_field()
    request_route: str = event_field()
    route_name: str = event_field()
