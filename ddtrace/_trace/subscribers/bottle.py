from types import TracebackType
from typing import Optional
from typing import cast

from ddtrace._trace._inferred_proxy import set_inferred_proxy_tags
from ddtrace._trace.span import Span
from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.contrib._events.bottle import BottleRequestEvent
from ddtrace.contrib._events.http import HttpRequestCompletedEvent
from ddtrace.contrib._events.http import HttpRequestDataEvent
from ddtrace.contrib._events.http import HttpRouteResolvedEvent
from ddtrace.contrib.internal import trace_utils
from ddtrace.contrib.internal.trace_utils import is_tracing_enabled
from ddtrace.internal import core
from ddtrace.internal.core.events import Event
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def _request_span(event: HttpRequestDataEvent) -> Optional[tuple[core.ExecutionContext[BottleRequestEvent], Span]]:
    """Find the span this event belongs to, with the context that owns it.

    The event stream carries no span identity, so the active context tree is the
    correlation key. Returns None when no span was started for the request.
    """
    if event.framework != "bottle":
        return None
    ctx: Optional[core.ExecutionContext[Event]] = core.current
    while ctx is not None:
        if ctx.identifier == BottleRequestEvent.event_name:
            # The identifier is the event name, so the event type follows from the match.
            request_ctx = cast(core.ExecutionContext[BottleRequestEvent], ctx)
            span = request_ctx.get_item("_inner_span")
            return None if span is None else (request_ctx, span)
        ctx = ctx.parent
    return None


class BottleRequestSubscriber(TracingSubscriber[BottleRequestEvent]):
    """Owns the Bottle request span, accumulating facts from the push-only event stream.

    The base class starts the span when the request context opens and finishes it when
    the context closes. In between, this subscriber is a plain listener: Bottle pushes
    events and never learns whether a span exists.
    """

    event_names = (BottleRequestEvent.event_name,)

    @classmethod
    def register(cls) -> None:
        super().register()
        core.on(HttpRouteResolvedEvent.event_name, cls._on_route_resolved, name=f"{cls.__name__}.route_resolved")
        core.on(HttpRequestCompletedEvent.event_name, cls._on_request_completed, name=f"{cls.__name__}.completed")

    @classmethod
    def unregister(cls) -> None:
        super().unregister()
        core.reset_listeners(HttpRouteResolvedEvent.event_name, cls._on_route_resolved)
        core.reset_listeners(HttpRequestCompletedEvent.event_name, cls._on_request_completed)

    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext[BottleRequestEvent]) -> None:
        # The one place that asks whether tracing is on. Producers push regardless;
        # skipping span creation here is the tracing product declining the work.
        if not is_tracing_enabled():
            return
        super()._on_context_started(ctx)

    @classmethod
    def _on_context_ended(
        cls,
        ctx: core.ExecutionContext[BottleRequestEvent],
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        if ctx.get_item("_inner_span") is None:
            return
        super()._on_context_ended(ctx, exc_info)

    @classmethod
    def _on_route_resolved(cls, event: HttpRouteResolvedEvent) -> None:
        found = _request_span(event)
        if found is None:
            return
        request = event.request
        # Bottle's historical resource omits a mount prefix that http.route keeps. It is
        # set as soon as the route is known because handlers and outer route plugins read
        # the resource off the active span mid-request.
        if request.route_pattern is not None:
            found[1].resource = f"{request.method} {request.route_pattern}"

    @classmethod
    def _on_request_completed(cls, event: HttpRequestCompletedEvent) -> None:
        found = _request_span(event)
        if found is None:
            return
        ctx, span = found

        request = event.request
        response = event.response
        status_code = response.status_code if response is not None else None
        if event.errors and not event.response_committed:
            # A prepared response that never reached the client is not what was served.
            status_code = 500

        if request.route_pattern is None:
            span.resource = f"{request.method} {status_code or 500}"

        try:
            trace_utils.set_http_meta(
                span=span,
                integration_config=ctx.event.integration_config,
                method=request.method,
                url=request.url,
                query=request.query_string,
                status_code=status_code,
                request_headers=dict(request.headers or ()),
                response_headers=dict(response.headers) if response is not None and response.headers else None,
                headers_are_case_sensitive=True,
                route=request.route,
            )
        except Exception:
            log.debug("bottle: error adding response tags", exc_info=True)

        set_inferred_proxy_tags(span, status_code)
