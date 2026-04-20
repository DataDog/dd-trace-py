import contextlib
import json
from types import TracebackType
from typing import Any
from typing import Optional

from ddtrace.appsec._asm_request_context import _get_asm_context
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import should_analyze_body_response
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._processor import AppSecSpanProcessor
from ddtrace.contrib._events.http_client import HttpClientEvents
from ddtrace.contrib._events.http_client import HttpClientRequestEvent
from ddtrace.contrib._events.http_client import HttpClientSendEvent
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.core.subscriber import ContextSubscriber


APPSEC_SSRF_ANALYZE_BODY_KEY = "appsec.ssrf_analyze_body"


class AppSecHttpxRequestContextSubscriber(ContextSubscriber[HttpClientRequestEvent]):
    event_names = (HttpClientEvents.HTTPX_REQUEST.value,)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[HttpClientRequestEvent]) -> None:
        if not AppSecSpanProcessor.rasp_enabled("ssrf"):
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
    ) -> None:
        exc_type, _, _ = exc_info
        if exc_type is not None:
            return

        if not AppSecSpanProcessor.rasp_enabled("ssrf"):
            return

        event: HttpClientRequestEvent = ctx.event
        if event.response_status_code is None or (300 <= event.response_status_code < 400):
            return

        addresses: dict[str, Any] = {
            "DOWN_RES_STATUS": str(event.response_status_code),
            "DOWN_RES_HEADERS": event.response_headers,
        }

        if ctx.get_item(APPSEC_SSRF_ANALYZE_BODY_KEY):
            if event.response is not None:
                with contextlib.suppress(Exception):
                    addresses["DOWN_RES_BODY"] = event.response.json()

        call_waf_callback(
            addresses,
            rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES,
        )


class AppSecHttpxSingleRequestContextSubscriber(ContextSubscriber[HttpClientSendEvent]):
    event_names = (HttpClientEvents.HTTPX_SEND_REQUEST.value,)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[HttpClientSendEvent]) -> None:
        if not AppSecSpanProcessor.rasp_enabled("ssrf"):
            return

        asm_context = _get_asm_context()
        if asm_context is None:
            return

        addresses = {
            EXPLOIT_PREVENTION.ADDRESS.SSRF: ctx.event.request_url,
            "DOWN_REQ_METHOD": ctx.event.request_method,
            "DOWN_REQ_HEADERS": ctx.event.request_headers,
        }

        analyze_body = ctx.find_item(APPSEC_SSRF_ANALYZE_BODY_KEY)
        body = ctx.event.request_body
        if analyze_body and body is not None:
            with contextlib.suppress(Exception):
                addresses["DOWN_REQ_BODY"] = json.loads(body())
        call_waf_callback(
            addresses,
            crop_trace="_on_context_started",
            rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_REQ,
        )
        asm_context.downstream_requests += 1
        if blocking_config := get_blocked():
            raise BlockingException(
                blocking_config, EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SSRF, ctx.event.request_url
            )

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext[HttpClientSendEvent],
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        exc_type, _, _ = exc_info
        if exc_type is not None:
            return

        if not AppSecSpanProcessor.rasp_enabled("ssrf"):
            return

        status = ctx.event.response_status_code
        if status is None:
            return

        if not (300 <= status < 400):
            return

        addresses = {
            "DOWN_RES_STATUS": str(status),
            "DOWN_RES_HEADERS": ctx.event.response_headers,
        }

        call_waf_callback(
            addresses,
            rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES,
        )
