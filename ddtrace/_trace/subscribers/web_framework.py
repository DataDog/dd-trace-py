from collections.abc import Iterator
from contextlib import contextmanager
from types import TracebackType
from typing import Optional

from ddtrace import config
from ddtrace._trace._inferred_proxy import _set_inferred_proxy_tags
from ddtrace._trace.events import WebFrameworkResponseEvent
from ddtrace._trace.http_meta import set_http_meta
from ddtrace._trace.http_tags import is_otel_non_error_status
from ddtrace._trace.http_tags import set_instrumentation_resource
from ddtrace._trace.otel.http.resource import record_initial_instrumentation_resource
from ddtrace._trace.otel.http.tags import set_method_tag
from ddtrace._trace.otel.http.tags import set_otel_resource_for_method
from ddtrace._trace.span import Span
from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace._trace.web_framework import WebFrameworkRequestEvent
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.logger import get_logger
from ddtrace.internal.otel_semantics import http as otel_http
from ddtrace.internal.span_bus import span_from_context
from ddtrace.trace import tracer


log = get_logger(__name__)

_DEFERRED_RESPONSE_PARENT = "_dd.web.response_parent"
_REQUEST_OWNER = "_dd.web.request_owner"


def _clear_response_parent(span: Span) -> None:
    span._set_ctx_item(_DEFERRED_RESPONSE_PARENT, None)


class WebFrameworkRequestSubscriber(TracingSubscriber[WebFrameworkRequestEvent]):
    """Shared tracing and deferred request ownership for web framework integrations."""

    event_names = (WebFrameworkRequestEvent.event_name,)

    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext[WebFrameworkRequestEvent]) -> None:
        if ctx.get_item("middleware") is not None:
            provider = tracer.context_provider
            active = provider.active()
            while isinstance(active, Span):
                suspended = active._get_ctx_item(_DEFERRED_RESPONSE_PARENT)
                if suspended is None:
                    break
                owner = ctx.parent
                while owner is not None and owner.get_item(_REQUEST_OWNER) is not suspended[0]:
                    owner = owner.parent
                if owner is not None:
                    break
                # A sibling response remains visible to outer middleware, but
                # must not become this request's implicit parent.
                provider.activate(suspended[1])
                active = provider.active()
            # Capture before the base subscriber activates distributed headers.
            ctx.set_item("request_parent_context", active)
            ctx.set_item(_REQUEST_OWNER, object())
        super()._on_context_started(ctx)

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

        # Setup can fail after response span creation but before the middleware
        # returns an iterable. Those failures also belong to the request span.
        request_exc_info = (None, None, None) if ctx.get_item("response_ready", False) else exc_info
        try:
            try:
                exc_type, exc_value, traceback = exc_info
                if exc_type is not None and exc_value is not None and not issubclass(exc_type, GeneratorExit):
                    response_span.set_exc_info(exc_type, exc_value, traceback)
            finally:
                response_span.finish()
        finally:
            super()._on_context_ended(ctx, request_exc_info)

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
        deferred_response = ctx.get_item("middleware") is not None
        # Flask/WSGI start_response hooks already tag headers. Replaying them at
        # completion would overwrite later span-tag edits or restore removed tags.
        res_headers = None if deferred_response else event.response_headers

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
                method=method or None,
                url=event.request_url or None,
                query=event.query,
                trace_query_string=event.trace_query_string,
                status_code=status_code,
                request_headers=None if request_tagged else event.request_headers,
                response_headers=response_headers,
                headers_are_case_sensitive=event.headers_case_sensitive,
                route=event.request_route,
                peer_ip=None if request_tagged else event.peer_ip,
                response_only=deferred_response,
            )
        except Exception:
            log.debug("%s: error adding response tags", event.integration_config.integration_name, exc_info=True)

        _set_inferred_proxy_tags(span, status_code)
        for tk, tv in event.additional_tags.items():
            span._set_attribute(tk, tv)


class WebFrameworkResponseSubscriber(Subscriber):
    """Keep a deferred response span inside its shared request lifecycle."""

    event_names = (WebFrameworkResponseEvent.event_name,)

    @classmethod
    def on_event(cls, event: WebFrameworkResponseEvent) -> None:
        ctx = event.request_context
        request_event = ctx.event
        response_span = tracer.start_span(event.operation_name, child_of=span_from_context(ctx), activate=True)
        response_span._set_attribute(COMPONENT, request_event.component)
        ctx.set_item("response_span", response_span)
        response_span._set_ctx_item(
            _DEFERRED_RESPONSE_PARENT, (ctx.get_item(_REQUEST_OWNER), ctx.get_item("request_parent_context"))
        )
        response_span._on_finish_callbacks.insert(0, _clear_response_parent)
        event.span_modifier(response_span, event.response)
        provider = tracer.context_provider
        active = provider.active()

        @contextmanager
        def response_context() -> Iterator[None]:
            nonlocal active
            previous = provider.active()
            suspended = response_span._get_ctx_item(_DEFERRED_RESPONSE_PARENT)
            response_span._set_ctx_item(_DEFERRED_RESPONSE_PARENT, None)
            try:
                provider.activate(active)
                yield
            finally:
                # A generator can suspend inside a child span across yields.
                active = provider.active()
                if not response_span.finished:
                    response_span._set_ctx_item(_DEFERRED_RESPONSE_PARENT, suspended)
                provider.activate(previous)
                # Completion may have finished the saved ambient span too.
                provider.active()

        event.response_context = response_context
