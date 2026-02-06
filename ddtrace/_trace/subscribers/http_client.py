from types import TracebackType
from typing import Optional
from typing import Tuple

from ddtrace._trace.subscribers._base import SpanTracingSubscriber
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.contrib import trace_utils
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.propagation.http import HTTPPropagator


log = get_logger(__name__)


class HttpClientTracingSubscriber(SpanTracingSubscriber):
    """Shared tracing logic for ALL HTTP client integrations.

    httpx, requests, aiohttp, etc. all share this subscriber.
    Adding a feature here applies to every HTTP client integration.
    """

    event_name = "http.client.request"

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
        span = ctx.span
        span._metrics[_SPAN_MEASURED_KEY] = 1

        request = ctx.get_item("request")
        config = ctx.get_item("config")

        if trace_utils.distributed_tracing_enabled(config):
            HTTPPropagator.inject(span.context, request.headers)

    @classmethod
    def on_ended(
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
            log.debug("%s: error adding tags", ctx.get_item("config").integration_name, exc_info=True)
