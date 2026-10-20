from dataclasses import dataclass
from types import TracebackType
from typing import Optional
from weakref import WeakValueDictionary

from ddtrace._trace._inferred_proxy import set_inferred_proxy_tags
from ddtrace._trace.span import Span
from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.contrib._events.http import HttpRequestCompletedEvent
from ddtrace.contrib._events.http import HttpRequestDataEvent
from ddtrace.contrib._events.http import HttpRouteResolvedEvent
from ddtrace.contrib._events.http import HttpServerRequestEvent
from ddtrace.contrib.internal import trace_utils
from ddtrace.contrib.internal.trace_utils import is_tracing_enabled
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


@dataclass(frozen=True)
class HttpServerConventions:
    """Where one framework's span differs from the shared HTTP server defaults.

    The defaults describe a WSGI framework whose headers keep their original case.
    Frameworks without an override use these defaults.
    """

    # ASGI servers hand over header names already lowercased, so the obfuscation and
    # allow-list matching in set_http_meta must not be told the case is meaningful.
    headers_are_case_sensitive: bool = True


# Emitting the shared contract is sufficient to create a request span. This registry
# supplies optional framework overrides, not an instrumentation allowlist.
_DEFAULT_CONVENTIONS = HttpServerConventions()
_CONVENTIONS: dict[str, HttpServerConventions] = {}


# Spans awaiting their request's remaining events, keyed by the request_id carried on
# every event for that request. Correlating by identity rather than by the active
# context tree means an event tags the right span no matter which thread or task
# dispatches it -- a framework may well report a committed response from somewhere
# other than the code that opened the context.
#
# The request lifecycle owns its context; this lookup must not keep it alive if
# end-event cleanup is missed. Normal completion still removes the entry explicitly.
# Requests never share a request_id, so concurrent requests touch their own key.
_REQUEST_CONTEXTS: WeakValueDictionary[object, core.ExecutionContext[HttpServerRequestEvent]] = WeakValueDictionary()


def _request_span(event: HttpRequestDataEvent) -> Optional[tuple[core.ExecutionContext[HttpServerRequestEvent], Span]]:
    """Find the span this event belongs to, with the context that owns it.

    Returns None when no span was started for the request, which is the normal case
    when tracing is disabled.
    """
    ctx = _REQUEST_CONTEXTS.get(event.request_id)
    if ctx is None:
        return None
    span: Optional[Span] = ctx.get_item("_inner_span")
    return None if span is None else (ctx, span)


class HttpServerRequestSubscriber(TracingSubscriber[HttpServerRequestEvent]):
    """Owns the server request span for every ported HTTP framework.

    The base class starts the span when the request context opens and finishes it when
    the context closes. In between, this subscriber is a plain listener: producers push
    events and never learn whether a span exists.

    One subscriber rather than one per framework, because the span is assembled the same
    way everywhere -- the registry, the phase handling, and the tag writing do not vary.
    What does vary lives in HttpServerConventions, which also keeps the dispatch cost
    flat: a single listener and a dict lookup instead of one listener per integration
    each testing whether the request is its own.
    """

    event_names = (HttpServerRequestEvent.event_name,)

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
    def _on_context_started(cls, ctx: core.ExecutionContext[HttpServerRequestEvent]) -> None:
        # The one place that asks whether tracing is on. Producers push regardless;
        # skipping span creation here is the tracing product declining the work.
        if not is_tracing_enabled():
            return
        super()._on_context_started(ctx)
        _REQUEST_CONTEXTS[ctx.event.request_id] = ctx

    @classmethod
    def _on_context_ended(
        cls,
        ctx: core.ExecutionContext[HttpServerRequestEvent],
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        # Drop the entry even if finishing raises, so a failure cannot strand the span.
        _REQUEST_CONTEXTS.pop(ctx.event.request_id, None)
        if ctx.get_item("_inner_span") is None:
            return
        super()._on_context_ended(ctx, exc_info)

    @classmethod
    def _on_route_resolved(cls, event: HttpRouteResolvedEvent) -> None:
        found = _request_span(event)
        if found is None:
            return
        request = event.request
        # route_pattern omits a mount prefix that http.route keeps, and the resource has
        # always been the unprefixed form. It is set as soon as the route is known because
        # handlers and outer plugins read the resource off the active span mid-request.
        if request.route_pattern is not None:
            found[1].resource = f"{request.method} {request.route_pattern}"

    @classmethod
    def _on_request_completed(cls, event: HttpRequestCompletedEvent) -> None:
        found = _request_span(event)
        if found is None:
            return
        ctx, span = found
        conventions = _CONVENTIONS.get(ctx.event.framework, _DEFAULT_CONVENTIONS)

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
                headers_are_case_sensitive=conventions.headers_are_case_sensitive,
                route=request.route,
            )
        except Exception:
            log.debug("%s: error adding response tags", ctx.event.framework, exc_info=True)

        set_inferred_proxy_tags(span, status_code)
