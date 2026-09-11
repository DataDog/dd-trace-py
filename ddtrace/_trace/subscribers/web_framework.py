from types import TracebackType
from typing import Optional

from ddtrace._trace._inferred_proxy import _set_inferred_proxy_tags
from ddtrace._trace.span import Span
from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace._trace.subscribers._base import _finish_span
from ddtrace._trace.subscribers._base import _start_span
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib.internal import trace_utils
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.span_bus import span_from_context


log = get_logger(__name__)


class WebFrameworkRequestSubscriber(TracingSubscriber):
    """Shared tracing logic for web framework integrations."""

    event_names = (WebFrameworkRequestEvent.event_name, "wsgi.__call__")

    @classmethod
    def _event(cls, ctx: core.ExecutionContext) -> Optional[WebFrameworkRequestEvent]:
        try:
            event = ctx.event
        except AttributeError:
            return None
        return event if isinstance(event, WebFrameworkRequestEvent) else None

    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext) -> None:
        event = cls._event(ctx)
        if event is None:
            return
        ctx.set_items(
            {
                "remote_addr": event.peer_ip,
                "headers": event.request_headers,
                "headers_case_sensitive": event.headers_case_sensitive,
            }
        )
        _start_span(ctx)
        ctx.set_item("req_span", span_from_context(ctx))
        if not getattr(ctx, "_dispatch_end_event", True):
            ctx.set_item("defer_span_finish", True)
        for handler in cls._started_handlers:
            handler(ctx)

    @classmethod
    def _on_context_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        if cls._event(ctx) is None:
            return
        try:
            for handler in cls._ended_handlers:
                handler(ctx, exc_info)
        finally:
            if getattr(ctx.event, "_end_span", True) and (
                not ctx.get_item("defer_span_finish", False) or exc_info[0] is not None
            ):
                _finish_span(ctx, exc_info)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext) -> None:
        event = cls._event(ctx)
        if event is None:
            return
        if event.allow_default_resource:
            event.set_resource = True

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
            span.resource = event.resource
        elif event.set_resource and status_code is not None:
            span.resource = f"{method} {status_code}"

        # Frameworks may resolve route fields after the event starts.
        for tag_name, tag_value in event.tags.items():
            span._set_attribute(tag_name, tag_value)

        if ctx.get_item("request_prepared", True) and not ctx.get_item("http_meta_set", False):
            try:
                trace_utils.set_http_meta(
                    span=span,
                    integration_config=event.integration_config,
                    method=method,
                    url=event.request_url,
                    # set_http_meta will check only integration_config to set or not query
                    # however, aiohttp support per-app trace_query_string config overrides
                    query=event.query if event.trace_query_string is None else None,
                    parsed_query=event.parsed_query,
                    status_code=status_code,
                    raw_uri=event.raw_uri,
                    request_headers=event.request_headers,
                    request_cookies=event.request_cookies,
                    request_path_params=event.request_path_params,
                    request_body=event.request_body,
                    response_headers=dict(res_headers) if res_headers is not None else None,
                    response_cookies=event.response_cookies,
                    peer_ip=event.peer_ip,
                    route=event.request_route,
                )
            except Exception:
                log.debug("%s: error adding tags", event.integration_config.integration_name, exc_info=True)

            _set_inferred_proxy_tags(span, status_code)
            for tk, tv in core.get_item("additional_tags", default=dict()).items():
                span._set_attribute(tk, tv)

        # aiohttp supports per-app trace_query_string overrides that may differ from
        # integration_config.trace_query_string.
        if event.trace_query_string and event.query is not None:
            span._set_attribute(http.QUERY_STRING, event.query)
