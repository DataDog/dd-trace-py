from types import TracebackType
from typing import Optional

from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace._trace.trace_handlers import _set_inferred_proxy_tags
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.web_framework import WebRequestEvent
from ddtrace.internal import core


class WebFrameworkRequestStartSubscriber(TracingSubscriber):
    event_names = (WebRequestEvent.event_name,)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext) -> None:
        event: WebRequestEvent = ctx.event
        if event.allow_default_resource:
            event.set_resource = True

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event: WebRequestEvent = ctx.event
        span = ctx.span
        status_code = event.response_status_code

        if event.set_resource and status_code is not None:
            span.resource = f"{event.request_method} {status_code}"

        trace_utils.set_http_meta(
            span=span,
            integration_config=event.config,
            method=event.request_method,
            url=event.request_url,
            status_code=status_code,
            query=event.request_query,
            request_headers=event.request_headers,
            response_headers=event.response_headers,
            route=event.request_pattern,
        )
        _set_inferred_proxy_tags(span, status_code)
