from contextvars import ContextVar
from types import TracebackType
from typing import Optional
from typing import cast

from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.http_client import HttpClientEvents
from ddtrace.contrib._events.http_client import HttpClientRequestEvent
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.propagation.http import HTTPPropagator


log = get_logger(__name__)

# Set to True by a higher-level integration that has already injected
# propagation headers (e.g., the botocore before-sign handler, which must
# inject before SigV4 signing so the trace headers are part of the canonical
# request). The shared HTTP subscriber checks this flag and skips its own
# injection to avoid clobbering or duplicating headers after signing.
# ContextVar provides per-thread isolation: concurrent requests in different
# threads each see their own value of this flag.
http_propagation_suppressed: ContextVar[bool] = ContextVar(
    "dd_http_propagation_suppressed", default=False
)


class HttpClientTracingSubscriber(TracingSubscriber):
    """Shared tracing logic for ALL HTTP client integrations.

    httpx, requests, aiohttp, etc. all share this subscriber.
    Adding a feature here applies to every HTTP client integration.
    """

    event_names = (HttpClientRequestEvent.event_name, HttpClientEvents.HTTPX_REQUEST.value)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext) -> None:
        event: HttpClientRequestEvent = ctx.event

        if http_propagation_suppressed.get():
            return

        if trace_utils.distributed_tracing_enabled(event.integration_config) and event.request_headers is not None:
            HTTPPropagator.inject(ctx.span.context, cast(dict[str, str], event.request_headers))

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event: HttpClientRequestEvent = ctx.event

        try:
            trace_utils.set_http_meta(
                ctx.span,
                event.integration_config,
                method=event.request_method,
                url=event.request_url,
                target_host=event.target_host,
                status_code=event.response_status_code,
                status_msg=event.response_status_msg,
                query=event.query,
                request_headers=event.request_headers,
                response_headers=event.response_headers,
                server_address=event.server_address,
                retries_remain=event.retries_remain,
            )
        except Exception:
            log.debug("%s: error adding tags", event.integration_config.integration_name, exc_info=True)
