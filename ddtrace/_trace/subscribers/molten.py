from ddtrace._trace.otel.http.tags import set_otel_resource_for_method
from ddtrace.contrib._events.molten import MoltenRouteEvent
from ddtrace.ext import http
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.settings._config import config
from ddtrace.internal.span_bus import span_from_context


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

        if config._otel_trace_semantics_enabled and request_event.request_method:
            # Name the span from the resolved route so sampling during the handler sees the final name.
            span = span_from_context(event.request_context)
            if span is not None:
                span._set_attribute(http.ROUTE, event.request_route)
                set_otel_resource_for_method(span, request_event.request_method, event.request_route)
