import contextlib
import json
from types import TracebackType
from typing import Any
from typing import Optional

from ddtrace.appsec._asm_request_context import _get_asm_context
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import should_analyze_body_response
from ddtrace.appsec._common_module_patches import _get_rasp_capability
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.contrib._events.http_client import HttpClientEvents
from ddtrace.contrib._events.http_client import HttpClientRequestEvent
from ddtrace.contrib._events.http_client import HttpClientSendEvent
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.core.subscriber import ContextSubscriber


APPSEC_SSRF_ANALYZE_BODY_KEY = "appsec.ssrf_analyze_body"


# AIDEV-NOTE: requests/httpx/urllib3 split logical requests and wire attempts at
# different stack frames. Keep outer request contexts and inner send contexts
# aligned with the library control flow so redirects, retries, and auth replays
# preserve identical AppSec semantics across integrations. requests re-enters
# Session.send for redirect/auth resend paths, so only the outermost
# requests.request context should own final-response handling.
def _has_ancestor_context(ctx: core.ExecutionContext[Any], identifier: str) -> bool:
    parent = ctx.parent
    while parent is not None:
        if parent.identifier == identifier:
            return True
        parent = parent.parent
    return False


class AppSecHttpClientRequestContextSubscriber(ContextSubscriber[HttpClientRequestEvent]):
    event_names = (
        HttpClientEvents.HTTPX_REQUEST.value,
        HttpClientEvents.REQUESTS_REQUEST.value,
        HttpClientEvents.URLLIB3_REQUEST.value,
    )

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[HttpClientRequestEvent]):
        if not _get_rasp_capability("ssrf"):
            return

        if (
            ctx.identifier == HttpClientEvents.REQUESTS_REQUEST.value
            and _has_ancestor_context(ctx, HttpClientEvents.REQUESTS_REQUEST.value)
        ):
            return

        asm_context = _get_asm_context()
        if asm_context is None:
            return

        analyze_body = should_analyze_body_response(asm_context)
        ctx.set_item(APPSEC_SSRF_ANALYZE_BODY_KEY, analyze_body)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext[HttpClientRequestEvent],
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ):
        exc_type, _, _ = exc_info
        if exc_type is not None:
            return

        if not _get_rasp_capability("ssrf"):
            return

        if ctx.get_item(APPSEC_SSRF_ANALYZE_BODY_KEY, None) is None:
            return

        event: HttpClientRequestEvent = ctx.event
        if event.response_status_code is None or (300 <= event.response_status_code < 400):
            return

        addresses: dict[str, Any] = {
            "DOWN_RES_STATUS": str(event.response_status_code),
            "DOWN_RES_HEADERS": event.response_headers,
        }

        if ctx.get_item(APPSEC_SSRF_ANALYZE_BODY_KEY) and event.response is not None:
            with contextlib.suppress(Exception):
                addresses["DOWN_RES_BODY"] = event.response.json()

        call_waf_callback(
            addresses,
            rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES,
        )


class AppSecHttpClientSingleRequestContextSubscriber(ContextSubscriber[HttpClientSendEvent]):
    event_names = (
        HttpClientEvents.HTTPX_SEND_REQUEST.value,
        HttpClientEvents.URLLIB3_SEND_REQUEST.value,
    )

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[HttpClientSendEvent]):
        if not _get_rasp_capability("ssrf"):
            return

        analyze_body = ctx.find_item(APPSEC_SSRF_ANALYZE_BODY_KEY, None)
        if analyze_body is None:
            return

        asm_context = _get_asm_context()
        if asm_context is None:
            return

        addresses = {
            EXPLOIT_PREVENTION.ADDRESS.SSRF: ctx.event.url,
            "DOWN_REQ_METHOD": ctx.event.request_method,
            "DOWN_REQ_HEADERS": ctx.event.request_headers,
        }

        body = ctx.event.request_body
        if analyze_body and body is not None:
            with contextlib.suppress(Exception):
                addresses["DOWN_REQ_BODY"] = json.loads(body())

        call_waf_callback(
            addresses,
            rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_REQ,
        )
        asm_context.downstream_requests += 1

        if blocking_config := get_blocked():
            raise BlockingException(
                blocking_config, EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SSRF, ctx.event.url
            )

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext[HttpClientSendEvent],
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ):
        exc_type, _, _ = exc_info
        if exc_type is not None:
            return

        if not _get_rasp_capability("ssrf"):
            return

        if ctx.find_item(APPSEC_SSRF_ANALYZE_BODY_KEY, None) is None:
            return

        status = ctx.event.response_status_code
        if status is None or not (300 <= status < 400):
            return

        addresses = {
            "DOWN_RES_STATUS": str(status),
            "DOWN_RES_HEADERS": ctx.event.response_headers,
        }

        call_waf_callback(
            addresses,
            rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES,
        )
