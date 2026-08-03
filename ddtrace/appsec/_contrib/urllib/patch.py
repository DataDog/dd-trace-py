from __future__ import annotations

import io
import json
from typing import Any
from typing import Callable
from typing import TypeVar
from typing import cast
from urllib.error import HTTPError

from ddtrace.appsec._asm_request_context import _get_asm_context
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import open_rasp_subcontext_scope
from ddtrace.appsec._asm_request_context import should_analyze_body_response
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._contrib.urllib.types import HTTPResponse
from ddtrace.appsec._contrib.urllib.types import Request
from ddtrace.appsec._metrics import report_rasp_skipped
from ddtrace.appsec._patch_utils import try_unwrap
from ddtrace.appsec._patch_utils import try_wrap_function_wrapper
from ddtrace.appsec._rasp import build_headers
from ddtrace.appsec._rasp import get_rasp_capability
from ddtrace.internal import core


T = TypeVar("T")


def patch() -> None:
    try_wrap_function_wrapper("urllib.request", "OpenerDirector.open", wrapped_open)


def unpatch() -> None:
    try_unwrap("urllib.request", "OpenerDirector.open")


def _parse_body(response: HTTPResponse) -> object | None:
    try:
        if response.length and response.headers.get("content-type") == "application/json":
            length = response.length
            body = response.read()
            response.fp = io.BytesIO(body)
            response.length = length
            parsed_body: object = json.loads(body)
            return parsed_body
    except Exception:
        return None
    return None


def wrapped_open(original: Callable[..., T], instance: object, args: tuple[Any, ...], kwargs: dict[str, Any]) -> T:
    if not get_rasp_capability("ssrf"):
        return original(*args, **kwargs)
    url = args[0] if args else kwargs.get("fullurl")
    if url is not None and url.__class__.__name__ == "Request":
        url = cast(Request, url).get_full_url()
    if not isinstance(url, str) or not url:
        return original(*args, **kwargs)
    context = _get_asm_context()
    if context is None:
        report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, False)
        return original(*args, **kwargs)
    use_body = should_analyze_body_response(context)
    with core.context_with_data("url_open_analysis", full_url=url, use_body=use_body):
        open_rasp_subcontext_scope()
        try:
            response = original(*args, **kwargs)
            if response.__class__.__name__ == "HTTPResponse":
                typed_response = cast(HTTPResponse, response)
            else:
                typed_response = None
            if typed_response is not None and not (300 <= typed_response.status < 400):
                addresses: dict[str, object] = {
                    "DOWN_RES_STATUS": str(typed_response.status),
                    "DOWN_RES_HEADERS": build_headers(typed_response.getheaders()),
                }
                if use_body:
                    addresses["DOWN_RES_BODY"] = _parse_body(typed_response)
                call_waf_callback(addresses, rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES)
        except HTTPError as error:
            headers = build_headers(error.headers.items()) if error.headers is not None else None
            call_waf_callback(
                {"DOWN_RES_STATUS": str(error.code), "DOWN_RES_HEADERS": headers},
                rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES,
            )
            raise
    return response
