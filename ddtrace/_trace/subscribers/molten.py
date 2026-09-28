from ddtrace.contrib._events.molten import MoltenRouteEvent
from ddtrace.internal.core.subscriber import Subscriber


MOLTEN_ROUTE = "molten.route"


class MoltenRouteSubscriber(Subscriber):
    event_names = (MoltenRouteEvent.event_name,)

    @classmethod
    def on_event(cls, event: MoltenRouteEvent) -> None:
        request_event = event.request_context.event
        request_event.request_route = event.request_route
        request_event.resource = event.resource
        request_event.set_resource = False

        if event.route_name:
            request_event.additional_tags[MOLTEN_ROUTE] = event.route_name
