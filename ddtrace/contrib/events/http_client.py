from dataclasses import dataclass
from enum import Enum
from types import TracebackType
from typing import Optional
from typing import Tuple

from ddtrace import config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib.events.base import ContextEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.propagation.http import HTTPPropagator


class HttpClientEvents(Enum):
    HTTP_REQUEST = "http.request"


@dataclass
class HttpClientRequestEvent(ContextEvent):
    """HTTP client request event"""

    def __init__(self, req):
        super().__init__(
            event_name=HttpClientEvents.HTTP_REQUEST.value,
            span_type=SpanTypes.HTTP,
            span_name=schematize_url_operation("http.request", protocol="http", direction=SpanDirection.OUTBOUND),
            call_trace=True,
            tags={COMPONENT: config.httpx.integration_name, SPAN_KIND: SpanKind.CLIENT},
        )
        self.request = req

    def on_context_started(self, ctx: core.ExecutionContext) -> None:
        span = ctx.span
        span._metrics[_SPAN_MEASURED_KEY] = 1

        request = ctx.get_item("request")

        if trace_utils.distributed_tracing_enabled(config.httpx):
            HTTPPropagator.inject(span.context, request.headers)

    def on_context_ended(
        self,
        ctx: core.ExecutionContext,
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        """Called when the HTTP request context ends, before span finishes."""
        span = ctx.span

        request = ctx.get_item("request")
        response = ctx.get_item("response")

        from ddtrace._trace.trace_handlers.common import httpx_url_to_str

        trace_utils.set_http_meta(
            span,
            config.httpx,
            method=request.method,
            url=httpx_url_to_str(request.url),
            target_host=request.url.host,
            status_code=response.status_code if response else None,
            query=request.url.query,
            request_headers=request.headers,
            response_headers=response.headers if response else None,
        )
