from dataclasses import dataclass
from types import TracebackType
from typing import Optional
from typing import Union
from typing import cast
from weakref import WeakValueDictionary

from ddtrace._trace._inferred_proxy import set_inferred_proxy_tags
from ddtrace._trace.span import Span
from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.contrib._events.http import HttpRequestCompletedEvent
from ddtrace.contrib._events.http import HttpRequestDataEvent
from ddtrace.contrib._events.http import HttpRouteResolvedEvent
from ddtrace.contrib._events.http import HttpServerRequestEvent
from ddtrace.contrib._events.http import HttpServerTracingDecisionEvent
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib.internal import trace_utils
from ddtrace.contrib.internal.trace_utils import is_tracing_enabled
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.span_bus import span_from_context


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


# Request contexts awaiting tracing policy or remaining observations, keyed by the
# request_id carried on every event. Correlating by identity rather than by the active
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

    Returns None when tracing is disabled, deferred, or suppressed for the route.
    """
    ctx = _REQUEST_CONTEXTS.get(event.request_id)
    if ctx is None:
        return None
    span: Optional[Span] = ctx.get_item("_inner_span")
    return None if span is None else (ctx, span)


class WebFrameworkRequestSubscriber(TracingSubscriber[Union[WebFrameworkRequestEvent, HttpServerRequestEvent]]):
    """Own the request span for legacy web events and the shared HTTP lifecycle.

    Legacy integrations keep their mutable WebFrameworkRequestEvent metadata.
    HTTP lifecycle producers publish observations separately and can defer span
    creation until route-level tracing policy is known. Both paths share one
    subscriber, while only HTTP lifecycle requests use the correlation registry.
    """

    event_names = (WebFrameworkRequestEvent.event_name, HttpServerRequestEvent.event_name)

    @classmethod
    def register(cls) -> None:
        super().register()
        core.on(
            HttpServerTracingDecisionEvent.event_name, cls._on_tracing_decision, name=f"{cls.__name__}.tracing_decision"
        )
        core.on(HttpRouteResolvedEvent.event_name, cls._on_route_resolved, name=f"{cls.__name__}.route_resolved")
        core.on(HttpRequestCompletedEvent.event_name, cls._on_request_completed, name=f"{cls.__name__}.completed")

    @classmethod
    def unregister(cls) -> None:
        super().unregister()
        core.reset_listeners(HttpServerTracingDecisionEvent.event_name, cls._on_tracing_decision)
        core.reset_listeners(HttpRouteResolvedEvent.event_name, cls._on_route_resolved)
        core.reset_listeners(HttpRequestCompletedEvent.event_name, cls._on_request_completed)

    @classmethod
    def _on_context_started(
        cls, ctx: core.ExecutionContext[Union[WebFrameworkRequestEvent, HttpServerRequestEvent]]
    ) -> None:
        if isinstance(ctx.event, WebFrameworkRequestEvent):
            super()._on_context_started(ctx)
            return
        http_ctx = cast(core.ExecutionContext[HttpServerRequestEvent], ctx)
        # Producers push regardless; the tracing product can decline the request.
        if not is_tracing_enabled():
            return
        _REQUEST_CONTEXTS[http_ctx.event.request_id] = http_ctx
        if not http_ctx.event.defer_span_start:
            super()._on_context_started(ctx)

    @classmethod
    def _ensure_span(cls, ctx: core.ExecutionContext[HttpServerRequestEvent]) -> None:
        if ctx.get_item("_http_tracing_decided"):
            return
        ctx.set_item("_http_tracing_decided", True)
        if ctx.get_item("_inner_span") is None and is_tracing_enabled():
            super()._on_context_started(
                cast(core.ExecutionContext[Union[WebFrameworkRequestEvent, HttpServerRequestEvent]], ctx)
            )
            resource = ctx.get_item("_http_resource")
            if resource is not None:
                # Route resolution happens before a deferred span exists, so the
                # resource it determined is applied here instead of being lost.
                ctx.get_item("_inner_span").resource = resource

    @classmethod
    def _on_tracing_decision(cls, event: HttpServerTracingDecisionEvent) -> None:
        ctx = _REQUEST_CONTEXTS.get(event.request_id)
        if ctx is None or not ctx.event.defer_span_start or ctx.get_item("_http_tracing_decided"):
            return
        if event.enabled:
            if event.request_headers is not None:
                # Both propagation and inferred proxy extraction use this snapshot.
                ctx.set_item("headers", dict(event.request_headers))
            cls._ensure_span(ctx)
        else:
            ctx.set_item("_http_tracing_decided", True)

    @classmethod
    def _on_context_ended(
        cls,
        ctx: core.ExecutionContext[Union[WebFrameworkRequestEvent, HttpServerRequestEvent]],
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        if isinstance(ctx.event, WebFrameworkRequestEvent):
            super()._on_context_ended(ctx, exc_info)
            return
        # Drop the entry even if finishing raises, so a failure cannot strand the span.
        _REQUEST_CONTEXTS.pop(ctx.event.request_id, None)
        if ctx.get_item("_inner_span") is None:
            return
        super()._on_context_ended(ctx, exc_info)

    @classmethod
    def _on_route_resolved(cls, event: HttpRouteResolvedEvent) -> None:
        ctx = _REQUEST_CONTEXTS.get(event.request_id)
        if ctx is None:
            return
        request = event.request
        # route_pattern omits a mount prefix that http.route keeps, and the resource has
        # always been the unprefixed form. It is set as soon as the route is known because
        # handlers and outer plugins read the resource off the active span mid-request.
        if request.route_pattern is None:
            return
        resource = f"{request.method} {request.route_pattern}"
        span: Optional[Span] = ctx.get_item("_inner_span")
        if span is None:
            ctx.set_item("_http_resource", resource)
        else:
            span.resource = resource

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

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[Union[WebFrameworkRequestEvent, HttpServerRequestEvent]]) -> None:
        event = ctx.event
        if not isinstance(event, WebFrameworkRequestEvent):
            return

        if event.allow_default_resource:
            event.set_resource = True

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext[Union[WebFrameworkRequestEvent, HttpServerRequestEvent]],
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event = ctx.event
        if not isinstance(event, WebFrameworkRequestEvent):
            return
        status_code = event.response_status_code
        method = event.request_method
        res_headers = event.response_headers

        span: Span = span_from_context(ctx)

        # event.resource can be updated at span finish time
        if event.resource:
            span.resource = event.resource
        elif event.set_resource and status_code is not None:
            span.resource = f"{method} {status_code}"

        try:
            trace_utils.set_http_meta(
                span=span,
                integration_config=event.integration_config,
                method=method,
                url=event.request_url,
                # set_http_meta checks only integration_config when deciding
                # whether to trace the query string. aiohttp supports a
                # per-application override instead.
                query=event.query if event.trace_query_string is None else None,
                status_code=status_code,
                request_headers=event.request_headers,
                response_headers=dict(res_headers) if res_headers is not None else None,
                headers_are_case_sensitive=event.headers_case_sensitive,
                route=event.request_route,
            )
        except Exception:
            log.debug("%s: error adding response tags", event.integration_config.integration_name, exc_info=True)

        # aiohttp supports per-app trace_query_string overrides that may differ from
        # integration_config.trace_query_string.
        if event.trace_query_string and event.query is not None:
            span._set_attribute(http.QUERY_STRING, event.query)

        set_inferred_proxy_tags(span, status_code)
        for tk, tv in event.additional_tags.items():
            span._set_attribute(tk, tv)
