import json
from typing import Any
from typing import Callable
from typing import TypeVar
from typing import cast

from ddtrace.appsec._asm_request_context import _get_asm_context
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._contrib.httplib.types import HTTPResponse
from ddtrace.appsec._patch_utils import try_unwrap
from ddtrace.appsec._patch_utils import try_wrap_function_wrapper
from ddtrace.appsec._rasp import get_rasp_capability
from ddtrace.appsec._rasp import must_block
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException


T = TypeVar("T")


def patch() -> None:
    try_wrap_function_wrapper("http.client", "HTTPConnection.request", wrapped_request)
    try_wrap_function_wrapper("http.client", "HTTPConnection.getresponse", wrapped_response)


def unpatch() -> None:
    try_unwrap("http.client", "HTTPConnection.request")
    try_unwrap("http.client", "HTTPConnection.getresponse")


def _build_headers(response: HTTPResponse) -> dict[str, str | list[str]]:
    result: dict[str, str | list[str]] = {}
    for key, value in response.getheaders():
        if key in result:
            current = result[key]
            result[key] = [current, value] if isinstance(current, str) else [*current, value]
        else:
            result[key] = value
    return result


def wrapped_request(original: Callable[..., T], instance: object, args: tuple[Any, ...], kwargs: dict[str, Any]) -> T:
    full_url = core.find_item("full_url")
    environment = _get_asm_context()
    if get_rasp_capability("ssrf") and full_url is not None and environment is not None:
        body = args[2] if len(args) > 2 else kwargs.get("body")
        headers = cast(dict[str, Any], args[3] if len(args) > 3 else kwargs.get("headers", {}))
        addresses: dict[str, object] = {
            EXPLOIT_PREVENTION.ADDRESS.SSRF: full_url,
            "DOWN_REQ_METHOD": args[0] if args else kwargs.get("method"),
            "DOWN_REQ_HEADERS": headers,
        }
        if (
            core.find_item("use_body", False)
            and (headers.get("Content-Type") or headers.get("content-type")) == "application/json"
        ):
            try:
                if isinstance(body, (str, bytes, bytearray)):
                    addresses["DOWN_REQ_BODY"] = json.loads(body)
            except Exception:
                pass  # nosec
        result = call_waf_callback(
            addresses,
            crop_trace="wrapped_request_A7F2C6E4D3B10958",
            rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_REQ,
        )
        environment.downstream_requests += 1
        core.discard_item("full_url")
        if result and must_block(result.actions):
            raise BlockingException(get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SSRF, full_url)
    return original(*args, **kwargs)


def wrapped_response(original: Callable[..., T], instance: object, args: tuple[Any, ...], kwargs: dict[str, Any]) -> T:
    response = original(*args, **kwargs)
    dynamic_response = cast(Any, response)
    environment = _get_asm_context()
    try:
        if (
            get_rasp_capability("ssrf")
            and dynamic_response.__class__.__name__ == "HTTPResponse"
            and environment is not None
        ):
            typed_response = cast(HTTPResponse, dynamic_response)
            status = typed_response.getcode()
            if 300 <= status < 400:
                call_waf_callback(
                    {"DOWN_RES_STATUS": str(status), "DOWN_RES_HEADERS": _build_headers(typed_response)},
                    rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES,
                )
    except Exception:
        pass  # nosec
    return response
