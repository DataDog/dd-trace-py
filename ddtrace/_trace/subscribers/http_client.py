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

# AIDEV-NOTE: Cross-module coordination primitive. A higher-level integration sets
# this True to tell this subscriber to skip its own injection — either because the
# integration already injected upstream (e.g. botocore's before-sign handler) or
# because distributed tracing is disabled and no headers should go out at any layer.
#
# Consumers today: ddtrace.contrib.internal.botocore.patch and
# ddtrace.contrib.internal.aiobotocore.patch (the SigV4 fix — botocore's
# before-sign event fires earlier than this subscriber's on_started, so
# headers can land in the canonical signed request).
#
# OWNERSHIP CONTRACT (do not break): the only setters of this contextvar
# are `patched_api_call` (botocore) and `_wrapped_api_call` (aiobotocore).
# Both MUST capture the Token and reset() in a try/finally. The before-sign
# event handler itself must not touch the contextvar — see the leak
# history in PR #18152 if you're tempted to change that. The handler was
# previously allowed to flip it True; that caused a real leak when
# early-return paths in patched_api_call bypassed the try/finally.
#
# ContextVar provides per-thread isolation: concurrent requests in different
# threads each see their own value of this flag.
_http_propagation_suppressed: ContextVar[bool] = ContextVar("dd_http_propagation_suppressed", default=False)


class HttpClientTracingSubscriber(TracingSubscriber):
    """Shared tracing logic for ALL HTTP client integrations.

    httpx, requests, aiohttp, etc. all share this subscriber.
    Adding a feature here applies to every HTTP client integration.
    """

    event_names = (HttpClientRequestEvent.event_name, HttpClientEvents.HTTPX_REQUEST.value)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext) -> None:
        event: HttpClientRequestEvent = ctx.event

        if _http_propagation_suppressed.get():
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
