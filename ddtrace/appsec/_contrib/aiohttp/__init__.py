from collections.abc import Coroutine
from collections.abc import Mapping
import json
from typing import Any
from typing import Optional

from ddtrace.appsec._asm_request_context import _on_context_ended
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_active_asm_context
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import iast_disabled_taint_sources
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
import ddtrace.vendor.xmltodict as xmltodict


logger = get_logger(__name__)

_PARSED_MEDIA_TYPES = frozenset((MediaType.JSON, MediaType.XML, MediaType.FORM_URLENCODED, MediaType.MULTIPART))


def _multidict_to_dict(multidict: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in multidict.keys():
        if key in result:
            continue
        values = multidict.getall(key)
        result[key] = values[0] if len(values) == 1 else values
    return result


def _form_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return getattr(value, "filename", value)


def _check_blocked() -> None:
    if block := get_blocked():
        raise BlockingException(block)


def _request_route(request: Any) -> Optional[str]:
    route = getattr(request.match_info, "route", None)
    resource = getattr(route, "resource", None)
    return getattr(resource, "canonical", None)


def _on_request_start(ctx: core.ExecutionContext[Any], request: Any) -> Optional[Coroutine[Any, Any, None]]:
    if not asm_config._asm_enabled or get_active_asm_context() is None:
        return None
    logger.debug("aiohttp::srb_on_request")
    span = span_from_context(ctx)
    request_headers = {k.lower(): v for k, v in request.headers.items()}
    request_headers.pop("cookie", None)
    if span is not None:
        trace_utils.set_http_meta(
            span,
            ctx.event.integration_config,
            method=request.method,
            request_headers=request_headers,
            raw_uri=f"{request.scheme}://{request.host}{request.raw_path}",
            parsed_query=_multidict_to_dict(request.query),
            peer_ip=request.remote,
            route=_request_route(request),
            request_cookies=dict(request.cookies),
            request_path_params=dict(request.match_info),
        )
    call_waf_callback()
    _check_blocked()
    if not request.body_exists:
        return None
    media_type = classify_media_type(request.headers.get("Content-Type"))
    if media_type not in _PARSED_MEDIA_TYPES:
        return None
    return _analyze_body(request, media_type)


async def _analyze_body(request: Any, media_type: MediaType) -> None:
    body: Any
    if media_type in (MediaType.FORM_URLENCODED, MediaType.MULTIPART):
        try:
            form = _multidict_to_dict(await request.post())
        except ValueError:
            logger.debug("aiohttp::failed to parse request form", exc_info=True)
            return
        body = {k: [_form_value(i) for i in v] if isinstance(v, list) else _form_value(v) for k, v in form.items()}
    else:
        raw_body = await request.read()
        try:
            with iast_disabled_taint_sources():
                if media_type is MediaType.JSON:
                    body = json.loads(raw_body) if raw_body else None
                else:
                    body = xmltodict.parse(raw_body)
        except Exception:
            logger.debug("aiohttp::failed to parse request body", exc_info=True)
            return
    if body:
        set_waf_address(SPAN_DATA_NAMES.REQUEST_BODY, body)
        call_waf_callback()
        _check_blocked()


def _set_response_addresses(status: int, headers: Mapping[str, str], body: Any) -> None:
    response_headers = {k.lower(): v for k, v in headers.items() if k.lower() != "set-cookie"}
    if isinstance(body, (bytes, bytearray)):
        response_headers.setdefault("content-length", str(len(body)))
    set_waf_address(SPAN_DATA_NAMES.RESPONSE_STATUS, str(status))
    set_waf_address(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES, response_headers)


def _on_response(request: Any, response: Any) -> None:
    if not asm_config._asm_enabled or get_active_asm_context() is None or get_blocked():
        return
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
