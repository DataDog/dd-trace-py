from types import TracebackType
from typing import Optional

from ddtrace import config
from ddtrace._trace.http_tags import set_instrumentation_resource
from ddtrace._trace.http_tags import set_query_string_tag
from ddtrace._trace.otel.http.resource import record_initial_instrumentation_resource
from ddtrace._trace.otel.http.tags import set_method_tag
from ddtrace._trace.otel.http.tags import set_otel_resource_for_method
from ddtrace._trace.span import Span
from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace._trace.trace_handlers import _set_inferred_proxy_tags
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib._events.web_framework import WebFrameworkRequestExceptionEvent
from ddtrace.contrib._events.web_framework import WebFrameworkResponseEvent
from ddtrace.contrib.internal import trace_utils
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.logger import get_logger
from ddtrace.internal.otel_semantics import http as otel_http
from ddtrace.internal.settings.standalone import standalone_config
from ddtrace.internal.span_bus import span_from_context
from ddtrace.trace import tracer


log = get_logger(__name__)


class WebFrameworkRequestSubscriberBase(TracingSubscriber):
    """Shared request ownership, including responses that outlive the application call."""

    @classmethod
    def _on_context_ended(
        cls,
        ctx: core.ExecutionContext[WebFrameworkRequestEvent],
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        if not ctx.event.span_creation_enabled:
            return
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
    def _on_context_started(cls, ctx: core.ExecutionContext[WebFrameworkRequestEvent]) -> None:
        # Other frameworks still require a span in their request lifecycle.
        if ctx.event.supports_spanless_lifecycle:
            tracer = core.root.get_item("tracer")
            apm_enabled = tracer is not None and tracer.enabled
            standalone_spans_required = tracer is not None and standalone_config.apm_opt_out
            # Standalone products need span lifecycle without enabling APM tracing.
            ctx.event.span_creation_enabled = apm_enabled or standalone_spans_required
        super()._on_context_started(ctx)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext) -> None:
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
        ctx: core.ExecutionContext,
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
                # set_http_meta checks only integration_config when deciding
                # whether to trace the query string. aiohttp supports a
                # per-application override instead.
                query=event.query if event.trace_query_string is None else None,
                query_tagged_separately=event.trace_query_string is not None,
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
            set_query_string_tag(span, event.query)

        _set_inferred_proxy_tags(span, status_code)
        for tk, tv in event.additional_tags.items():
            span._set_attribute(tk, tv)


class WebFrameworkRequestExceptionSubscriber(Subscriber):
    auto_register = False
    event_names = (WebFrameworkRequestExceptionEvent.event_name,)

    @classmethod
    def on_event(cls, event: WebFrameworkRequestExceptionEvent) -> None:
        # The context may have released its event at response preparation. Retain
        # the original tracing decision, including when AppSec borrowed a parent span.
        if not event.request_event.span_creation_enabled:
            return
        span = event.request_context.get_item("_inner_span")
        exc_type, exc_value, traceback = event.exc_info
        if span is not None and exc_type is not None and exc_value is not None and traceback is not None:
            span.set_exc_info(exc_type, exc_value, traceback)


class WebFrameworkResponseSubscriber(Subscriber):
    """Keep a deferred response span inside its shared request lifecycle."""

    auto_register = False
    event_names = (WebFrameworkResponseEvent.event_name,)

    @classmethod
    def on_event(cls, event: WebFrameworkResponseEvent) -> None:
        ctx = event.request_context
        request_event = ctx.event
        if not request_event.span_creation_enabled:
            return

        response_span = tracer.start_span(event.operation_name, child_of=span_from_context(ctx), activate=True)
        response_span._set_attribute(COMPONENT, request_event.component)
        ctx.set_item("response_span", response_span)
        event.span_modifier(response_span, event.response)
