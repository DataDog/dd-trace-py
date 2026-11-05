"""Temporary routing between request events and the legacy span processor.

The processor selects a route before span binding. Remember it weakly so completion
survives context exit and processor replacement without retaining unfinished spans.
Remove the registry and integration allowlist with AppSecIastSpanProcessor once
request events own all supported integration lifecycles.
"""

from weakref import WeakKeyDictionary

from ddtrace.appsec._iast._iast_request_context import _iast_end_request
from ddtrace.appsec._iast._iast_request_context_base import _iast_start_request
from ddtrace.contrib._events.span_lifecycle import SpanLifecycleEvents
from ddtrace.internal import core
from ddtrace.internal.core.events import Event
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.native._native import SpanData
from ddtrace.internal.span_bus import span_from_context


_EVENT_MANAGED_REQUESTS: dict[str, frozenset[str]] = {
    "asgi.request": frozenset({"fastapi"}),
    "wsgi.__call__": frozenset({"flask"}),
}
# True means completion is pending; False keeps a completed event route idempotent.
_SPAN_ROUTES: WeakKeyDictionary[SpanData, bool] = WeakKeyDictionary()


def uses_request_events(ctx: core.ExecutionContext[Event], span: SpanData) -> bool:
    integrations = _EVENT_MANAGED_REQUESTS.get(ctx.identifier)
    if integrations is None:
        return False
    integration = ctx.get_item("integration_config")
    # Keep legacy ownership unless both lifecycle notifications can be handled.
    if not (
        getattr(integration, "integration_name", None) in integrations
        and (ctx.identifier != "asgi.request" or ctx.get_item("scope", {}).get("type") == "http")
        and not ctx.get_item("is_subapp", False)
        and core.has_listeners(f"{SpanLifecycleEvents.SPAN_STARTED.value}.{ctx.identifier}")
        and core.has_listeners(f"{SpanLifecycleEvents.SPAN_FINISHING.value}.{ctx.identifier}")
    ):
        return False
    # Start runs before binding; later manual WEB spans stay with the processor.
    bound_span = ctx.get_item("_inner_span")
    return bound_span is None or bound_span is span


class IastRequestStartedSubscriber(Subscriber):
    auto_register = False
    event_names = tuple(
        f"{SpanLifecycleEvents.SPAN_STARTED.value}.{identifier}" for identifier in _EVENT_MANAGED_REQUESTS
    )

    @classmethod
    def on_event(cls, ctx: core.ExecutionContext[Event]) -> None:
        span = span_from_context(ctx)
        if _SPAN_ROUTES.get(span, False):
            _iast_start_request(span)


class IastRequestFinishingSubscriber(Subscriber):
    auto_register = False
    event_names = tuple(
        f"{SpanLifecycleEvents.SPAN_FINISHING.value}.{identifier}" for identifier in _EVENT_MANAGED_REQUESTS
    )

    @classmethod
    def on_event(cls, ctx: core.ExecutionContext[Event]) -> None:
        span = span_from_context(ctx)
        if _SPAN_ROUTES.get(span, False):
            _SPAN_ROUTES[span] = False
            _iast_end_request(span, context=ctx)
