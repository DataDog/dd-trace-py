from collections.abc import Coroutine
from collections.abc import Mapping
import json
import re
from typing import Any
from typing import Optional
from typing import Union
from urllib.parse import parse_qsl

from ddtrace.appsec._asm_request_context import _on_context_ended
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_active_asm_context
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import iast_disabled_taint_sources
from ddtrace.appsec._asm_request_context import set_block_request_callable
from ddtrace.appsec._asm_request_context import set_blocked
from ddtrace.appsec._asm_request_context import set_body_response
from ddtrace.appsec._asm_request_context import set_waf_address
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.appsec._utils import Block_config
from ddtrace.contrib.internal import trace_utils
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils.http import MediaType
from ddtrace.internal.utils.http import _get_blocked_template
from ddtrace.internal.utils.http import classify_media_type
from ddtrace.internal.utils.http import parse_form_multipart
import ddtrace.vendor.xmltodict as xmltodict


logger = get_logger(__name__)

_PARSED_MEDIA_TYPES = frozenset((MediaType.JSON, MediaType.XML, MediaType.FORM_URLENCODED, MediaType.MULTIPART))
_COOKIE_ESCAPE = re.compile(r"\\(?:([0-3][0-7][0-7])|(.))")


def _multidict_to_dict(multidict: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in multidict.keys():
        if key in result:
            continue
        values = multidict.getall(key)
        result[key] = values[0] if len(values) == 1 else values
    return result


def _headers_to_dict(headers: Any, excluded: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in headers.items():
        key = key.lower()
        if key == excluded:
            continue
        result[key] = f"{result[key]}, {value}" if key in result else value
    return result


def _unquote_cookie(value: str) -> str:
    if len(value) < 2 or value[0] != '"' or value[-1] != '"':
        return value
    return _COOKIE_ESCAPE.sub(lambda m: chr(int(m[1], 8)) if m[1] else m[2], value[1:-1])


def _closing_quote(raw: str, index: int) -> Optional[int]:
    while index < len(raw):
        if raw[index] == "\\":
            index += 2
        elif raw[index] == '"':
            return index
        else:
            index += 1
    return None


def _split_cookies(raw: str) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start <= len(raw):
        end = raw.find(";", start)
        end = len(raw) if end < 0 else end
        equal = raw.find("=", start, end)
        if equal >= 0:
            value = equal + 1
            while value < end and raw[value] in " \t":
                value += 1
            if raw.startswith('"', value) and (closing := _closing_quote(raw, value + 1)) is not None:
                end = raw.find(";", closing)
                end = len(raw) if end < 0 else end
        chunks.append(raw[start:end])
        start = end + 1
    return chunks


def _parse_cookies(headers: Any) -> dict[str, Union[str, list[str]]]:
    cookies: dict[str, list[str]] = {}
    for chunk in _split_cookies("; ".join(headers.getall("Cookie", ()))):
        key, separator, value = chunk.partition("=")
        if not separator:
            key, value = "", key
        key, value = key.strip(), value.strip()
        if key or value:
            cookies.setdefault(key, []).append(_unquote_cookie(value))
    return {key: values[0] if len(values) == 1 else values for key, values in cookies.items()}


def _check_blocked() -> None:
    if block := get_blocked():
        raise BlockingException(block)


def _block_request() -> None:
    if not get_blocked():
        set_blocked(Block_config())
    _check_blocked()


def _on_request_start(
    ctx: core.ExecutionContext[Any], request: Any, max_body_size: int
) -> Optional[Coroutine[Any, Any, Optional[bytes]]]:
    if not asm_config._asm_enabled or get_active_asm_context() is None:
        return None
    logger.debug("aiohttp::srb_on_request")
    set_block_request_callable(_block_request)
    span = span_from_context(ctx)
    if span is not None:
        trace_utils.set_http_meta(
            span,
            ctx.event.integration_config,
            method=request.method,
            request_headers=_headers_to_dict(request.headers, "cookie"),
            raw_uri=f"{request.scheme}://{request.host}{request.raw_path}",
            parsed_query=_multidict_to_dict(request.query),
            peer_ip=request.remote,
            route=ctx.event.request_route,
            headers_are_case_sensitive=True,
            request_cookies=_parse_cookies(request.headers),
            request_path_params=dict(request.match_info),
        )
    call_waf_callback()
    _check_blocked()
    if not request.body_exists:
        return None
    media_type = classify_media_type(request.headers.get("Content-Type"))
    if media_type not in _PARSED_MEDIA_TYPES:
        return None
    content_length = request.content_length
    if content_length is None or content_length > max_body_size:
        return None
    if request.headers.get("Content-Encoding", "identity").lower() != "identity":
        return None
    return _analyze_body(request, media_type)


async def _analyze_body(request: Any, media_type: MediaType) -> Optional[bytes]:
    try:
        raw_body: bytes = await request.content.read()
    except Exception:
        logger.debug("aiohttp::failed to read request body", exc_info=True)
        return None
    body: Any = None
    charset = request.charset or "utf-8"
    try:
        with iast_disabled_taint_sources():
            if media_type is MediaType.JSON:
                body = json.loads(raw_body.decode(charset)) if raw_body else None
            elif media_type is MediaType.XML:
                body = xmltodict.parse(raw_body)
            else:
                text_body = raw_body.decode(charset, errors="replace")
                if media_type is MediaType.FORM_URLENCODED:
                    form: dict[str, list[str]] = {}
                    for key, value in parse_qsl(text_body.rstrip(), keep_blank_values=True, encoding=charset):
                        form.setdefault(key, []).append(value)
                    body = {key: values[0] if len(values) == 1 else values for key, values in form.items()}
                else:
                    body = parse_form_multipart(text_body, request.headers)
    except Exception:
        logger.debug("aiohttp::failed to parse request body", exc_info=True)
        return raw_body
    if body:
        set_waf_address(SPAN_DATA_NAMES.REQUEST_BODY, body)
        call_waf_callback()
        _check_blocked()
    return raw_body


def _set_response_addresses(status: int, headers: Mapping[str, str], body: Any) -> None:
    response_headers = _headers_to_dict(headers, "set-cookie")
    if isinstance(body, (bytes, bytearray)):
        response_headers.setdefault("content-length", str(len(body)))
    set_waf_address(SPAN_DATA_NAMES.RESPONSE_STATUS, str(status))
    set_waf_address(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES, response_headers)


def _on_response(request: Any, response: Any) -> None:
    if not asm_config._asm_enabled or get_active_asm_context() is None:
        return
    _check_blocked()
    logger.debug("aiohttp::srb_on_response")
    body = getattr(response, "body", None)
    _set_response_addresses(response.status, response.headers, body)
    if isinstance(body, (bytes, bytearray)) and asm_config._api_security_feature_active:
        if asm_config._api_security_parse_response_body:
            set_body_response(bytes(body))
    call_waf_callback()
    _check_blocked()


def _on_block(request: Any, block: Block_config) -> tuple[int, dict[str, str], bytes]:
    if block.type == "none":
        headers = {"Content-Type": "text/plain; charset=utf-8", "Location": block.location}
        content = b""
    else:
        headers = {"Content-Type": block.content_type}
        content = _get_blocked_template(block.content_type, block.block_id).encode("utf-8")
    _set_response_addresses(block.status_code, headers, content)
    return block.status_code, headers, content


def listen() -> None:
    core.on("aiohttp.request.start", _on_request_start, "aiohttp_request_body")
    core.on("aiohttp.request.response", _on_response)
    core.on("aiohttp.request.block", _on_block, "aiohttp_block_response")
    core.on("aiohttp.request.end", _on_context_ended)
