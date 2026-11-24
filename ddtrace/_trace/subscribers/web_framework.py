from types import TracebackType
from typing import Optional

from ddtrace import config
from ddtrace._trace._inferred_proxy import _set_inferred_proxy_tags
from ddtrace._trace.http_meta import set_http_meta
from ddtrace._trace.http_tags import is_otel_non_error_status
from ddtrace._trace.http_tags import set_instrumentation_resource
from ddtrace._trace.otel.http.resource import record_initial_instrumentation_resource
from ddtrace._trace.otel.http.tags import set_method_tag
from ddtrace._trace.otel.http.tags import set_otel_resource_for_method
from ddtrace._trace.span import Span
from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace._trace.subscribers._base import _finish_span
from ddtrace._trace.web_framework import WebFrameworkRequestEvent
from ddtrace._trace.web_framework import WSGIRequestEvent
from ddtrace._trace.web_framework import WSGIResponseEvent
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.core.subscriber import ContextSubscriber
from ddtrace.internal.logger import get_logger
from ddtrace.internal.otel_semantics import http as otel_http
from ddtrace.internal.span_bus import find_span_on_context
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.span_bus import store_span_on_context
from ddtrace.trace import tracer


log = get_logger(__name__)


class WebFrameworkRequestSubscriber(TracingSubscriber[WebFrameworkRequestEvent]):
    """Shared tracing logic for web framework integrations."""

    event_names = (WebFrameworkRequestEvent.event_name,)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[WebFrameworkRequestEvent]) -> None:
        event: WebFrameworkRequestEvent = ctx.event

        if event.allow_default_resource:
            event.set_resource = True

        if config._otel_trace_semantics_enabled and event.request_method:
            span: Span = span_from_context(ctx)
            set_method_tag(span, event.request_method)
            # Set the currently known resource before sampling; preserve span-start callback replacements.
            record_initial_instrumentation_resource(span, event.resource)
            if event.request_route:
                span._set_attribute(otel_http.ROUTE, event.request_route)
            set_otel_resource_for_method(span, event.request_method, event.request_route)

        if event.tag_request_on_start:
            try:
                set_http_meta(
                    span=span_from_context(ctx),
                    integration_config=event.integration_config,
                    request_headers=event.request_headers,
                    headers_are_case_sensitive=event.headers_case_sensitive,
                    peer_ip=event.peer_ip,
                )
            except Exception:
                log.debug("%s: error adding request tags", event.integration_config.integration_name, exc_info=True)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext[WebFrameworkRequestEvent],
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event: WebFrameworkRequestEvent = ctx.event
        status_code = event.response_status_code
        method = event.request_method
        # Integrations that tag at entry (WSGI start_response hooks) skip replaying headers
        # here, which would overwrite later span-tag edits or restore removed tags.
        res_headers = event.response_headers if event.tag_request_on_end else None

        span: Span = span_from_context(ctx)

        if (
            event.response_from_exception
            and status_code is not None
            and is_otel_non_error_status(status_code)
            and exc_info[0] is not None
        ):
            # Framework HTTP exceptions carry responses; their status determines OTel errors.
            span._ignore_exception(exc_info[0])

        # event.resource can be updated at span finish time
        if event.resource:
            set_instrumentation_resource(span, event.resource)
        elif event.set_resource and status_code is not None:
            set_instrumentation_resource(span, f"{method} {status_code}")

        try:
            response_headers = dict(res_headers) if res_headers is not None else None
        except Exception:
            log.debug("%s: error reading response headers", event.integration_config.integration_name, exc_info=True)
            response_headers = None

        request_tagged = event.tag_request_on_start
        try:
            set_http_meta(
                span=span,
                integration_config=event.integration_config,
                method=method,
                url=event.request_url,
                query=event.query,
                trace_query_string=event.trace_query_string,
                status_code=status_code,
                request_headers=None if request_tagged else event.request_headers,
                response_headers=response_headers,
                headers_are_case_sensitive=event.headers_case_sensitive,
                route=event.request_route,
                peer_ip=None if request_tagged else event.peer_ip,
                response_only=not event.tag_request_on_end,
            )
        except Exception:
            log.debug("%s: error adding response tags", event.integration_config.integration_name, exc_info=True)

        _set_inferred_proxy_tags(span, status_code)
        for tk, tv in event.additional_tags.items():
            span._set_attribute(tk, tv)


class WSGIRequestSubscriber(WebFrameworkRequestSubscriber):
    """Trace a WSGI request whose context ends when its response iterable completes."""

    event_names = (WSGIRequestEvent.event_name,)

    @classmethod
    def _finish_span(
        cls,
        ctx: core.ExecutionContext[WebFrameworkRequestEvent],
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        # The service is resolved at entry. Span modifiers and applications may replace it
        # before the response finishes streaming, so do not resolve it again here.
        _finish_span(ctx, exc_info, resolve_service=False)


class WSGIResponseSubscriber(ContextSubscriber[WSGIResponseEvent]):
    """Trace a WSGI response iterable as a child of its request span."""

    event_names = (WSGIResponseEvent.event_name,)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[WSGIResponseEvent]) -> None:
        event = ctx.event
        request_ctx = ctx.parent
        parent = find_span_on_context(request_ctx) if request_ctx is not None else None
        span = tracer.start_span(event.operation_name, child_of=parent, activate=True)
        span._set_attribute(COMPONENT, event.component)
        store_span_on_context(ctx, span)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext[WSGIResponseEvent],
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        span = find_span_on_context(ctx)
        if span is None:
            return
        try:
            _, exc_value, traceback = exc_info
            # A server closing the response early raises GeneratorExit; that is not an error.
            if exc_value is not None and not isinstance(exc_value, GeneratorExit):
                span.set_exc_info(type(exc_value), exc_value, traceback)
        finally:
            span.finish()
