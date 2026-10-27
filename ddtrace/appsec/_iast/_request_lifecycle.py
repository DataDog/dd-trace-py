"""IAST request events and temporary routing while integrations migrate."""

from weakref import WeakKeyDictionary

from ddtrace.appsec._iast._iast_request_context import _iast_end_request
from ddtrace.appsec._iast._iast_request_context_base import _iast_start_request
from ddtrace.contrib._events.web_request import WebRequestCompleting
from ddtrace.contrib._events.web_request import WebRequestReady
from ddtrace.internal import core
from ddtrace.internal.core.events import Event
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.native._native import SpanData


# Remove this policy and bookkeeping with the legacy processor.
_EVENT_MANAGED_REQUESTS: dict[str, frozenset[str]] = {"asgi.request": frozenset({"fastapi"})}
# Keep routing for the span's lifetime: it may finish before the request completes.
_SPAN_ROUTES: WeakKeyDictionary[SpanData, bool] = WeakKeyDictionary()


def uses_request_events(ctx: core.ExecutionContext[Event], span: SpanData) -> bool:
    integrations = _EVENT_MANAGED_REQUESTS.get(ctx.identifier)
    if integrations is None:
        return False
    integration = ctx.get_item("integration_config")
    if not (
        getattr(integration, "integration_name", None) in integrations
        and core.has_listeners(WebRequestReady.event_name)
        and core.has_listeners(WebRequestCompleting.event_name)
    ):
        return False
    # Tracing binds the request span after on_span_start returns. Later WEB spans
    # under that context must keep their own legacy callbacks.
    bound_span = ctx.get_item("_inner_span")
    return bound_span is None or bound_span is span


class IastRequestReadySubscriber(Subscriber):
    auto_register = False
    event_names = (WebRequestReady.event_name,)

    @classmethod
    def on_event(cls, event: WebRequestReady) -> None:
        if event.span in _SPAN_ROUTES:
            _iast_start_request(event.span, context=event.ctx)


class IastRequestCompletingSubscriber(Subscriber):
    auto_register = False
    event_names = (WebRequestCompleting.event_name,)

    @classmethod
    def on_event(cls, event: WebRequestCompleting) -> None:
        if event.span in _SPAN_ROUTES:
            _iast_end_request(event.span, context=event.ctx)
