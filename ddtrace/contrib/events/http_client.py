from enum import Enum
from types import TracebackType
from typing import Optional
from typing import Tuple

from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.core.events import ContextEvent
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.propagation.http import HTTPPropagator


log = get_logger(__name__)


class HttpClientEvents(Enum):
    HTTP_REQUEST = "http.request"


class HttpClientRequestEvent(ContextEvent):
    """HTTP client request event"""

    event_name = HttpClientEvents.HTTP_REQUEST.value

    def __new__(cls, req, config, url, query, target_host):
        return {
            "event_name": cls.event_name,
            "span_type": SpanTypes.HTTP,
            "span_name": schematize_url_operation("http.request", protocol="http", direction=SpanDirection.OUTBOUND),
            "call_trace": True,
            "request": req,
            "tags": {COMPONENT: config.integration_name, SPAN_KIND: SpanKind.CLIENT},
            "config": config,
            "url": url,
            "query": query,
            "target_host": target_host,
        }

    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
        span = ctx.span
        span._metrics[_SPAN_MEASURED_KEY] = 1

        request = ctx.get_item("request")
        config = ctx.get_item("config")

        if trace_utils.distributed_tracing_enabled(config):
            HTTPPropagator.inject(span.context, request.headers)

    @classmethod
    def _on_context_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        span = ctx.span

        request = ctx.get_item("request")
        response = ctx.get_item("response")

        response_headers = dict(getattr(response, "headers", {}))

        try:
            trace_utils.set_http_meta(
                span,
                ctx.get_item("config"),
                method=request.method,
                url=ctx.get_item("url"),
                target_host=ctx.get_item("target_host"),
                status_code=response.status_code if response is not None else None,
                query=ctx.get_item("query"),
                request_headers=request.headers,
                response_headers=response_headers,
            )
        except Exception:
            log.debug("http.client: error adding tags", exc_info=True)
