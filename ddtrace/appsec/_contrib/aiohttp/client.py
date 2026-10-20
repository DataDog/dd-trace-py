import asyncio
from collections.abc import Awaitable
from collections.abc import Iterable
import json
from typing import Any
from typing import Callable
from typing import Optional
from typing import Union

from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_active_asm_context
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import open_rasp_subcontext_scope
from ddtrace.appsec._asm_request_context import should_analyze_body_response
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._metrics import report_rasp_skipped
from ddtrace.appsec._patch_utils import try_unwrap
from ddtrace.appsec._patch_utils import try_wrap_function_wrapper
from ddtrace.appsec._rasp import _must_block
from ddtrace.appsec._rasp import get_rasp_capability
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

_USE_BODY = "appsec.aiohttp_client.use_body"


def _headers(items: Iterable[tuple[str, str]]) -> dict[str, Union[str, list[str]]]:
    result: dict[str, Union[str, list[str]]] = {}
    for key, value in items:
        key = key.lower()
        previous = result.get(key)
        if previous is None:
            result[key] = value
        elif isinstance(previous, list):
            previous.append(value)
        else:
            result[key] = [previous, value]
    return result


def _is_json(headers: Any) -> bool:
    return (headers.get("Content-Type") or "").partition(";")[0].strip().lower() == "application/json"


def _request_body(req: Any) -> Optional[bytes]:
    body = getattr(req, "body", None)
    if not isinstance(body, (bytes, bytearray)):
        body = getattr(body, "_value", None)
    return bytes(body) if isinstance(body, (bytes, bytearray)) else None


async def _wrapped_request(
    original: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    if not get_rasp_capability("ssrf"):
        return await original(*args, **kwargs)
    env = get_active_asm_context()
    if env is None:
        report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, False)
        return await original(*args, **kwargs)
    with core.context_with_data("rasp.ssrf.aiohttp"):
        core.set_item(_USE_BODY, should_analyze_body_response(env))
        open_rasp_subcontext_scope()
        return await original(*args, **kwargs)


def _analyze_request(req: Any) -> None:
    env = get_active_asm_context()
    if env is None or core.find_item(_USE_BODY) is None:
        return
    url = str(req.url)
    addresses: dict[str, Any] = {
        EXPLOIT_PREVENTION.ADDRESS.SSRF: url,
        "DOWN_REQ_METHOD": req.method,
        "DOWN_REQ_HEADERS": _headers(req.headers.items()),
    }
    if core.find_item(_USE_BODY) and _is_json(req.headers):
        body = _request_body(req)
        if body:
            try:
                addresses["DOWN_REQ_BODY"] = json.loads(body)
            except Exception:
                log.debug("aiohttp client: invalid JSON request body", exc_info=True)
    res = call_waf_callback(addresses, crop_trace="_wrapped_connect", rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_REQ)
    env.downstream_requests += 1
    if res and _must_block(res.actions):
        raise BlockingException(get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SSRF, url)


async def _wrapped_connect(
    original: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    try:
        _analyze_request(args[0] if args else kwargs["req"])
    except Exception:
        log.debug("aiohttp client: SSRF request analysis failed", exc_info=True)
    return await original(*args, **kwargs)


async def _response_body(response: Any) -> Optional[Any]:
    from aiohttp.streams import StreamReader

    if response.content_length is None or not _is_json(response.headers):
        return None
    body: bytes = await response.read()
    stream = StreamReader(response._protocol, max(len(body), 2**16), loop=asyncio.get_running_loop())
    stream.feed_data(body)
    stream.feed_eof()
    response.content = stream
    return json.loads(body)


async def _analyze_response(response: Any) -> None:
    if get_active_asm_context() is None or core.find_item(_USE_BODY) is None:
        return
    addresses: dict[str, Any] = {
        "DOWN_RES_STATUS": str(response.status),
        "DOWN_RES_HEADERS": _headers(response.headers.items()),
    }
    if not (300 <= response.status < 400) and core.find_item(_USE_BODY):
        try:
            body = await _response_body(response)
            if body is not None:
                addresses["DOWN_RES_BODY"] = body
        except Exception:
            log.debug("aiohttp client: response body analysis failed", exc_info=True)
    call_waf_callback(addresses, rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES)


async def _wrapped_start(
    original: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    response = await original(*args, **kwargs)
    try:
        await _analyze_response(response)
    except Exception:
        log.debug("aiohttp client: SSRF response analysis failed", exc_info=True)
    return response


def patch() -> None:
    try_wrap_function_wrapper("aiohttp.client", "ClientSession._request", _wrapped_request)
    try_wrap_function_wrapper("aiohttp.connector", "BaseConnector.connect", _wrapped_connect)
    try_wrap_function_wrapper("aiohttp.client_reqrep", "ClientResponse.start", _wrapped_start)


def unpatch() -> None:
    try_unwrap("aiohttp.client", "ClientSession._request")
    try_unwrap("aiohttp.connector", "BaseConnector.connect")
    try_unwrap("aiohttp.client_reqrep", "ClientResponse.start")
