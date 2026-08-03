import json
from typing import Any
from typing import Callable
from typing import Iterable
from typing import Mapping
from typing import TypeVar
from typing import Union
from typing import cast
from urllib.parse import urlunparse

from ddtrace.appsec._asm_request_context import _get_asm_context
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import open_rasp_subcontext_scope
from ddtrace.appsec._asm_request_context import should_analyze_body_response
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._contrib.urllib3.types import HTTPConnectionPool
from ddtrace.appsec._contrib.urllib3.types import Response
from ddtrace.appsec._metrics import report_rasp_skipped
from ddtrace.appsec._patch_utils import try_unwrap
from ddtrace.appsec._patch_utils import try_wrap_function_wrapper
from ddtrace.appsec._rasp import get_rasp_capability
from ddtrace.appsec._rasp import must_block
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException


T = TypeVar("T")
HeaderSource = Union[Mapping[str, object], Iterable[tuple[str, object]]]


def patch() -> None:
    try_wrap_function_wrapper("urllib3.connectionpool", "HTTPConnectionPool._make_request", wrapped_make_request)
    try_wrap_function_wrapper("urllib3.connectionpool", "HTTPConnectionPool.urlopen", wrapped_urlopen)
    try_wrap_function_wrapper("urllib3._request_methods", "RequestMethods.request", wrapped_request)
    try_wrap_function_wrapper("urllib3.request", "RequestMethods.request", wrapped_request)


def unpatch() -> None:
    # AIDEV-NOTE: This teardown is used for test lifecycle isolation. It removes every AppSec wrapper
    # and lazy module hook, including HTTPConnectionPool.urlopen; production does not call it while
    # the tracing integration's wrapper is layered on top.
    try_unwrap("urllib3.connectionpool", "HTTPConnectionPool._make_request")
    try_unwrap("urllib3.connectionpool", "HTTPConnectionPool.urlopen")
    try_unwrap("urllib3._request_methods", "RequestMethods.request")
    try_unwrap("urllib3.request", "RequestMethods.request")


def _parse_headers(headers: object) -> dict[str, object]:
    try:
        return dict(cast(HeaderSource, headers))
    except Exception:
        return {}


def wrapped_make_request(
    original: Callable[..., T], instance: object, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> T:
    # "full_url" and "use_body" are published by the outer wrappers that own the caller-visible URL
    # (wrapped_urlopen and wrapped_request below, plus urllib's wrapped_open) and consumed here and
    # in ddtrace/appsec/_contrib/httplib/patch.py.
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
            crop_trace=wrapped_make_request.__name__,
            rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_REQ,
        )
        environment.downstream_requests += 1
        if result and must_block(result.actions):
            raise BlockingException(get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SSRF, full_url)
        # The call stays inside the context so nested response wrappers see the same subcontext.
        response = original(*args, **kwargs)
    return response


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
        if response.__class__.__name__ == "Response":
            typed_response = cast(Response, response)
        else:
            typed_response = None
        if typed_response is not None and not (300 <= typed_response.status_code < 400):
            addresses: dict[str, object] = {
                "DOWN_RES_STATUS": str(typed_response.status_code),
                "DOWN_RES_HEADERS": dict(typed_response.headers),
            }
            if use_body:
                try:
                    addresses["DOWN_RES_BODY"] = typed_response.json()
                except Exception:
                    pass  # nosec
            call_waf_callback(addresses, rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES)
    return response
