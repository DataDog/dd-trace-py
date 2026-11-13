from types import TracebackType
from typing import Optional

from ddtrace import config
from ddtrace._trace.http_tags import set_instrumentation_resource
from ddtrace._trace.otel.http.resource import record_initial_instrumentation_resource
from ddtrace._trace.otel.http.tags import set_method_tag
from ddtrace._trace.otel.http.tags import set_otel_resource_for_method
from ddtrace._trace.span import Span
from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace._trace.trace_handlers import _set_inferred_proxy_tags
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib._events.web_framework import WebFrameworkResponseEvent
from ddtrace.contrib.internal import trace_utils
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.logger import get_logger
from ddtrace.internal.otel_semantics import http as otel_http
from ddtrace.internal.span_bus import span_from_context
from ddtrace.trace import tracer


log = get_logger(__name__)


class WebFrameworkRequestSubscriberBase(TracingSubscriber[WebFrameworkRequestEvent]):
    """Shared request ownership, including responses that outlive the application call."""

    @classmethod
    def _on_context_ended(
        cls,
        ctx: core.ExecutionContext[WebFrameworkRequestEvent],
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        if ctx.get_item("middleware") is not None:
            # WSGI resolved its initial service at entry. Keep later span overrides,
            # including when the application failed before creating a response span.
            ctx.set_item("resolve_service_on_finish", False)
        response_span = ctx.get_item("response_span")
        if response_span is None:
            super()._on_context_ended(ctx, exc_info)
            return

        # Deferred WSGI failures belong to the response span. The request's
        # status and error attribution were established before streaming.
        try:
            try:
                exc_type, exc_value, traceback = exc_info
                if exc_type is not None and exc_value is not None and not issubclass(exc_type, GeneratorExit):
                    response_span.set_exc_info(exc_type, exc_value, traceback)
            finally:
                response_span.finish()
        finally:
            super()._on_context_ended(ctx, (None, None, None))


class WebFrameworkRequestSubscriber(WebFrameworkRequestSubscriberBase):
    """Shared tracing logic for web framework integrations."""

    auto_register = False
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

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext[WebFrameworkRequestEvent],
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event: WebFrameworkRequestEvent = ctx.event
        status_code = event.response_status_code
        method = event.request_method
        res_headers = event.response_headers

        span: Span = span_from_context(ctx)

        # event.resource can be updated at span finish time
        if event.resource:
            set_instrumentation_resource(span, event.resource)
        elif event.set_resource and status_code is not None:
            set_instrumentation_resource(span, f"{method} {status_code}")

        try:
            trace_utils.set_http_meta(
                span=span,
                integration_config=event.integration_config,
                method=method or None,
                url=event.request_url or None,
                query=event.query,
                trace_query_string=event.trace_query_string,
                status_code=status_code,
                request_headers=event.request_headers,
                response_headers=dict(res_headers) if res_headers is not None else None,
                headers_are_case_sensitive=event.headers_case_sensitive,
                route=event.request_route,
            )
        except Exception:
            log.debug("%s: error adding response tags", event.integration_config.integration_name, exc_info=True)

        _set_inferred_proxy_tags(span, status_code)
        for tk, tv in event.additional_tags.items():
            span._set_attribute(tk, tv)


class WebFrameworkResponseSubscriber(Subscriber):
    """Keep a deferred response span inside its shared request lifecycle."""

    auto_register = False
    event_names = (WebFrameworkResponseEvent.event_name,)

    @classmethod
    def on_event(cls, event: WebFrameworkResponseEvent) -> None:
        ctx = event.request_context
        request_event = ctx.event
        response_span = tracer.start_span(event.operation_name, child_of=span_from_context(ctx), activate=True)
        response_span._set_attribute(COMPONENT, request_event.component)
        ctx.set_item("response_span", response_span)
        event.span_modifier(response_span, event.response)
