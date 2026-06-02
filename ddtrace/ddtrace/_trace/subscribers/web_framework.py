from types import TracebackType
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace._trace.trace_handlers import _set_inferred_proxy_tags
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib.internal import trace_utils
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class WebFrameworkRequestSubscriber(TracingSubscriber):
    """Shared tracing logic for web framework integrations."""

    event_names = (WebFrameworkRequestEvent.event_name,)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext) -> None:
        event: WebFrameworkRequestEvent = ctx.event

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

        span: Span = ctx.span

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
                # set_http_meta will check only integration_config to set or not query
                # however, aiohttp support per-app trace_query_string config overrides
                query=event.query if event.trace_query_string is None else None,
                status_code=status_code,
                request_headers=event.request_headers,
                response_headers=dict(res_headers) if res_headers is not None else None,
                route=event.request_route,
            )
        except Exception:
            log.debug("%s: error adding tags", event.integration_config.integration_name, exc_info=True)

        # aiohttp supports per-app trace_query_string overrides that may differ from
        # integration_config.trace_query_string.
        if event.trace_query_string and event.query is not None:
            span._set_attribute(http.QUERY_STRING, event.query)

        _set_inferred_proxy_tags(span, status_code)
        for tk, tv in core.get_item("additional_tags", default=dict()).items():
            span._set_attribute(tk, tv)
