from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
import json
from typing import Any
from typing import Optional

from ddtrace.appsec._asm_request_context import _call_waf
from ddtrace.appsec._asm_request_context import _call_waf_first
from ddtrace.appsec._asm_request_context import _on_context_ended
from ddtrace.appsec._asm_request_context import _set_headers_and_response
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._utils import Block_config
from ddtrace.contrib.internal.trace_utils_base import _get_request_header_user_agent
from ddtrace.contrib.internal.trace_utils_base import _set_url_tag
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal.constants import RESPONSE_HEADERS
from ddtrace.internal.core import ExecutionContext
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.utils import http as http_utils
from ddtrace.internal.utils.http import parse_form_multipart
import ddtrace.vendor.xmltodict as xmltodict


logger = get_logger(__name__)

_ASGIReceive = Callable[[], Awaitable[Optional[dict[str, Any]]]]


async def _on_asgi_request_parse_body(receive: _ASGIReceive, headers: Mapping[str, str]) -> tuple[_ASGIReceive, Any]:
    if asm_config._asm_enabled:
        # This must not be imported globally due to 3rd party patching timeline
        import asyncio

        more_body = True
        body_parts: list[bytes] = []
        try:
            while more_body:
                data_received = await asyncio.wait_for(receive(), asm_config._fast_api_async_body_timeout)
                if data_received is None:
                    more_body = False
                if isinstance(data_received, dict):
                    more_body = data_received.get("more_body", False)
                    body_parts.append(data_received.get("body", b""))
        except asyncio.TimeoutError:
            pass
        except Exception:
            return receive, None
        body = b"".join(body_parts)

        async def receive_wrapped(once: list[bool] = [True]) -> Optional[dict[str, Any]]:
            if once[0]:
                once[0] = False
                return {"type": "http.request", "body": body, "more_body": more_body}
            return await receive()

        try:
            content_type = headers.get("content-type") or headers.get("Content-Type")
            if content_type in ("application/json", "text/json"):
                if body is None or body == b"":
                    req_body = None
                else:
                    req_body = json.loads(body.decode())
            elif content_type in ("application/xml", "text/xml"):
                req_body = xmltodict.parse(body)
            elif content_type == "text/plain":
                req_body = None
            else:
                req_body = parse_form_multipart(body.decode(), headers) or None
            return receive_wrapped, req_body
        except Exception:
            return receive_wrapped, None

    return receive, None


def _asgi_make_block_content(ctx: ExecutionContext, url: str) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    middleware = ctx.get_item("middleware")
    req_span = ctx.get_item("req_span")
    headers = ctx.get_item("headers")
    environ = ctx.get_item("environ")
    if req_span is None:
        raise ValueError("request span not found")
    block_config = get_blocked() or Block_config()
    ctype = None
    if block_config.type == "none":
        content = b""
        resp_headers = [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"location", block_config.location.encode()),
        ]
    else:
        content = http_utils._get_blocked_template(block_config.content_type, block_config.block_id).encode("UTF-8")
        # ctype = f"{ctype}; charset=utf-8" can be considered at some point
        resp_headers = [(b"content-type", block_config.content_type.encode())]
    status = block_config.status_code
    try:
        req_span._set_attribute(RESPONSE_HEADERS + ".content-length", str(len(content)))
        if ctype is not None:
            req_span._set_attribute(RESPONSE_HEADERS + ".content-type", ctype)
        req_span._set_attribute(http.STATUS_CODE, str(status))
        query_string = environ.get("QUERY_STRING")
        _set_url_tag(middleware.integration_config, req_span, url, query_string)
        if query_string and middleware._config.trace_query_string:
            req_span._set_attribute(http.QUERY_STRING, query_string)
        method = environ.get("REQUEST_METHOD")
        if method:
            req_span._set_attribute(http.METHOD, method)
        user_agent = _get_request_header_user_agent(headers, headers_are_case_sensitive=True)
        if user_agent:
            req_span._set_attribute(http.USER_AGENT, user_agent)
    except Exception as e:
        logger.warning("Could not set some span tags on blocked request: %s", str(e))
    resp_headers.append((b"Content-Length", str(len(content)).encode()))
    return status, resp_headers, content


def listen() -> None:
    core.on("asgi.request.parse.body", _on_asgi_request_parse_body, "await_receive_and_body")
    core.on("asgi.block.started", _asgi_make_block_content, "status_headers_content")

    core.on("asgi.start_request", _call_waf_first)
    core.on("asgi.start_response", _call_waf)
    core.on("asgi.finalize_response", _set_headers_and_response)

    core.on("context.ended.asgi.__call__", _on_context_ended)
