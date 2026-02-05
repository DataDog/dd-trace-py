from ddtrace._trace.trace_subscribers._base import SpanTracingSubscriber
from ddtrace.contrib import trace_utils
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
    def on_started(cls, ctx, call_trace=True, **kwargs):
        span = ctx.span
        integration_config = ctx.get_item("integration_config")
        request_headers = ctx.get_item("request_headers")
        if integration_config and request_headers is not None:
            if trace_utils.distributed_tracing_enabled(integration_config):
                HTTPPropagator.inject(span.context, request_headers)

    @classmethod
    def on_ended(cls, ctx, exc_info):
        span = ctx.span
        try:
            trace_utils.set_http_meta(
                span,
                ctx.get_item("integration_config"),
                method=ctx.get_item("method"),
                url=ctx.get_item("url"),
                target_host=ctx.get_item("target_host"),
                status_code=ctx.get_item("status_code"),
                query=ctx.get_item("query"),
                request_headers=ctx.get_item("request_headers"),
                response_headers=ctx.get_item("response_headers"),
            )
        except Exception:
            log.debug("http.client: error adding tags", exc_info=True)
