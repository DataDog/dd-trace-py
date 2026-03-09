import json

from ddtrace.appsec._asm_request_context import _get_asm_context
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import should_analyze_body_response
from ddtrace.appsec._common_module_patches import _get_rasp_capability
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.contrib._events.httpx import HttpxRequestEvent
from ddtrace.contrib.internal.httpx.utils import httpx_url_to_str
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.core import ExecutionContext


APPSEC_SSRF_ANALYZE_BODY_KEY = "appsec.ssrf_analyze_body"


def _on_httpx_request_started(ctx: ExecutionContext) -> None:
    if not _get_rasp_capability("ssrf"):
        return
    asm_context = _get_asm_context()
    if asm_context is None:
        return

    analyze_body = should_analyze_body_response(asm_context)
    ctx.set_item(APPSEC_SSRF_ANALYZE_BODY_KEY, analyze_body)


def _on_httpx_client_send_single_request_started(ctx: ExecutionContext) -> None:
    if not _get_rasp_capability("ssrf"):
        return

    asm_context = _get_asm_context()
    if asm_context is None:
        return

    request = ctx.get_item("request")
    if request is None:
        return

    raw_url = httpx_url_to_str(request.url)

    addresses = {
        EXPLOIT_PREVENTION.ADDRESS.SSRF: raw_url,
        "DOWN_REQ_METHOD": request.method,
        "DOWN_REQ_HEADERS": request.headers,
    }

    analyze_body = ctx.find_item(APPSEC_SSRF_ANALYZE_BODY_KEY)
    if analyze_body:
        try:
            addresses["DOWN_REQ_BODY"] = json.loads(request.content)
        except Exception:
            pass  # nosec
    call_waf_callback(
        addresses,
        rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_REQ,
    )
    asm_context.downstream_requests += 1
    if blocking_config := get_blocked():
        raise BlockingException(blocking_config, EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SSRF, raw_url)


def _on_httpx_client_send_single_request_ended(ctx: ExecutionContext, exc_info) -> None:
    exc_type, _, _ = exc_info
    if exc_type is not None:
        return

    if not _get_rasp_capability("ssrf"):
        return

    response = ctx.get_item("response")
    if not response or not (300 <= response.status_code < 400):
        return

    addresses = {
        "DOWN_RES_STATUS": str(response.status_code),
        "DOWN_RES_HEADERS": response.headers,
    }

    call_waf_callback(
        addresses,
        rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES,
    )


def _on_httpx_request_ended(ctx: ExecutionContext, exc_info) -> None:
    exc_type, _, _ = exc_info
    if exc_type is not None:
        return

    if not _get_rasp_capability("ssrf"):
        return

    event: HttpxRequestEvent = ctx.event
    if event.response_status_code is None or (300 <= event.response_status_code < 400):
        return

    addresses = {
        "DOWN_RES_STATUS": str(event.response_status_code),
        "DOWN_RES_HEADERS": event.response_headers,
    }

    if ctx.get_item(APPSEC_SSRF_ANALYZE_BODY_KEY):
        response = ctx.get_item("response")
        if response is not None:
            try:
                addresses["DOWN_RES_BODY"] = response.json()
            except Exception:
                pass  # nosec

    call_waf_callback(
        addresses,
        rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES,
    )


def listen():
    core.on("context.started.httpx.client._send_single_request", _on_httpx_client_send_single_request_started)
    core.on("context.ended.httpx.client._send_single_request", _on_httpx_client_send_single_request_ended)
    core.on("context.started.httpx.request", _on_httpx_request_started)
    core.on("context.ended.httpx.request", _on_httpx_request_ended)
