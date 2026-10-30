"""Temporary routing between request events and the legacy span processor."""

from weakref import WeakKeyDictionary

from ddtrace.appsec._iast._iast_request_context import _iast_end_request
from ddtrace.appsec._iast._iast_request_context_base import _iast_start_request
from ddtrace.contrib._events.web_request import WebRequestCompleting
from ddtrace.contrib._events.web_request import WebRequestReady
from ddtrace.internal import core
from ddtrace.internal.core.events import Event
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.native._native import SpanData


_EVENT_MANAGED_REQUESTS: dict[str, frozenset[str]] = {"asgi.request": frozenset({"fastapi"})}
_SPAN_ROUTES: WeakKeyDictionary[SpanData, bool] = WeakKeyDictionary()


def uses_request_events(ctx: core.ExecutionContext[Event], span: SpanData) -> bool:
    integrations = _EVENT_MANAGED_REQUESTS.get(ctx.identifier)
    if integrations is None:
        return False
    integration = ctx.get_item("integration_config")
    if not (
        getattr(integration, "integration_name", None) in integrations
        and ctx.get_item("scope", {}).get("type") == "http"
        and core.has_listeners(WebRequestReady.event_name)
        and core.has_listeners(WebRequestCompleting.event_name)
    ):
        return False
    # Start runs before binding; later manual WEB spans stay with the processor.
    bound_span = ctx.get_item("_inner_span")
    return bound_span is None or bound_span is span


class IastRequestReadySubscriber(Subscriber):
    auto_register = False
    event_names = (WebRequestReady.event_name,)

    @classmethod
    def on_event(cls, event: WebRequestReady) -> None:
        if event.span in _SPAN_ROUTES:
            _iast_start_request(event.span)


class IastRequestCompletingSubscriber(Subscriber):
    auto_register = False
    event_names = (WebRequestCompleting.event_name,)

    @classmethod
    def on_event(cls, event: WebRequestCompleting) -> None:
        if event.span in _SPAN_ROUTES:
            _iast_end_request(event.span, context=event.ctx)
