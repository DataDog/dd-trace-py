import json
from typing import Any
from typing import Callable
from typing import TypeVar
from typing import cast
from urllib.parse import urlunparse

from ddtrace.appsec._asm_request_context import _get_asm_context
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import open_rasp_subcontext_scope
from ddtrace.appsec._asm_request_context import should_analyze_body_response
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._contrib.urllib3.types import HTTPConnectionPool
from ddtrace.appsec._metrics import report_rasp_skipped
from ddtrace.appsec._patch_utils import try_unwrap
from ddtrace.appsec._patch_utils import try_wrap_function_wrapper
from ddtrace.appsec._rasp import get_rasp_capability
from ddtrace.appsec._rasp import must_block
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException


T = TypeVar("T")


def patch() -> None:
    try_wrap_function_wrapper("urllib3.connectionpool", "HTTPConnectionPool._make_request", wrapped_make_request)
    try_wrap_function_wrapper("urllib3.connectionpool", "HTTPConnectionPool.urlopen", wrapped_urlopen)
    try_wrap_function_wrapper("urllib3._request_methods", "RequestMethods.request", wrapped_request)
    try_wrap_function_wrapper("urllib3.request", "RequestMethods.request", wrapped_request)


def unpatch() -> None:
    try_unwrap("urllib3.connectionpool", "HTTPConnectionPool._make_request")
    try_unwrap("urllib3.connectionpool", "HTTPConnectionPool.urlopen")
    try_unwrap("urllib3._request_methods", "RequestMethods.request")
    try_unwrap("urllib3.request", "RequestMethods.request")


def _parse_headers(headers: object) -> dict[object, object]:
    try:
        return dict(cast(Any, headers))
    except Exception:
        return {}


def wrapped_make_request(
    original: Callable[..., T], instance: object, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> T:
    full_url = core.find_item("full_url")
    environment = _get_asm_context()
    if not (get_rasp_capability("ssrf") and full_url is not None and environment is not None):
        return original(*args, **kwargs)

    core.discard_item("full_url")
    with core.context_with_data("rasp.ssrf.urllib3"):
        open_rasp_subcontext_scope()
        use_body = core.find_item("use_body", False)
        method = args[1] if len(args) > 1 else kwargs.get("method")
        body = args[3] if len(args) > 3 else kwargs.get("body")
        headers = _parse_headers(args[4] if len(args) > 4 else kwargs.get("headers", {}))
        addresses: dict[str, object] = {
            EXPLOIT_PREVENTION.ADDRESS.SSRF: full_url,
            "DOWN_REQ_METHOD": method,
            "DOWN_REQ_HEADERS": headers,
        }
        content_type = headers.get("Content-Type") or headers.get("content-type")
        if use_body and content_type == "application/json":
            try:
                if isinstance(body, (str, bytes, bytearray)):
                    addresses["DOWN_REQ_BODY"] = json.loads(body)
            except Exception:
                pass  # nosec
        result = call_waf_callback(
            addresses,
            crop_trace="wrapped_urllib3_make_request_6D4E8B2A1F095C73",
            rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_REQ,
        )
        environment.downstream_requests += 1
        if result and must_block(result.actions):
            raise BlockingException(get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SSRF, full_url)
        return original(*args, **kwargs)
    raise AssertionError("unreachable")


def _absolute_url(instance: HTTPConnectionPool, path: str) -> str:
    try:
        port = instance.port
        netloc = "{}:{}".format(instance.host, port) if port and port not in (80, 443) else str(instance.host)
        return urlunparse((instance.scheme, netloc, path, "", "", ""))
    except Exception:  # nosec
        return path


def wrapped_urlopen(original: Callable[..., T], instance: object, args: tuple[Any, ...], kwargs: dict[str, Any]) -> T:
    full_url = args[1] if len(args) > 1 else kwargs.get("url")
    if isinstance(full_url, str) and full_url.startswith("/"):
        full_url = _absolute_url(cast(HTTPConnectionPool, instance), full_url)
    if core.find_item("full_url") is None:
        core.set_item("full_url", full_url)
    try:
        return original(*args, **kwargs)
    finally:
        core.discard_item("full_url")


def wrapped_request(original: Callable[..., T], instance: object, args: tuple[Any, ...], kwargs: dict[str, Any]) -> T:
    if not get_rasp_capability("ssrf"):
        return original(*args, **kwargs)

    url = args[1] if len(args) > 1 else kwargs.get("url")
    if not isinstance(url, str) or not url:
        return original(*args, **kwargs)
    context = _get_asm_context()
    if context is None:
        report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, False)
        return original(*args, **kwargs)

    use_body = should_analyze_body_response(context)
    with core.context_with_data("url_open_analysis", full_url=url, use_body=use_body):
        open_rasp_subcontext_scope()
        response = original(*args, **kwargs)
        dynamic_response = cast(Any, response)
        if dynamic_response.__class__.__name__ == "Response" and not (300 <= dynamic_response.status_code < 400):
            addresses: dict[str, object] = {
                "DOWN_RES_STATUS": str(dynamic_response.status_code),
                "DOWN_RES_HEADERS": dict(dynamic_response.headers),
            }
            if use_body:
                try:
                    addresses["DOWN_RES_BODY"] = dynamic_response.json()
                except Exception:
                    pass  # nosec
            call_waf_callback(addresses, rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES)
        return response
    raise AssertionError("unreachable")
