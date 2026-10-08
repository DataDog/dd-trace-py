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
from ddtrace.appsec._asm_request_context import iast_disabled_taint_sources
from ddtrace.appsec._utils import Block_config
from ddtrace.contrib.internal.trace_utils_base import _get_request_header_user_agent
from ddtrace.contrib.internal.trace_utils_base import _set_url_tag
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal.constants import RESPONSE_HEADERS
from ddtrace.internal.core import ExecutionContext
from ddtrace.internal.core.events import Event
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.utils import http as http_utils
from ddtrace.internal.utils.http import MediaType
from ddtrace.internal.utils.http import classify_media_type
from ddtrace.internal.utils.http import parse_form_multipart
import ddtrace.vendor.xmltodict as xmltodict


logger = get_logger(__name__)

_ASGIReceive = Callable[[], Awaitable[Optional[dict[str, Any]]]]


async def _on_asgi_request_parse_body(receive: _ASGIReceive, headers: Mapping[str, str]) -> tuple[_ASGIReceive, Any]:
    if asm_config._asm_enabled:
        # This must not be imported globally due to 3rd party patching timeline
        import asyncio

        body_limit = asm_config._asm_body_parsing_size_limit
        if body_limit <= 0:
            return receive, None
        content_length = headers.get("content-length") or headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > body_limit:
                    # Known over-limit size: consume nothing, leave the app's
                    # receive channel untouched.
                    return receive, None
            except ValueError:
                pass

        more_body = True
        body_parts: list[bytes] = []
        replay_parts: list[bytes] = []
        buffered = 0
        over_limit = False
        try:
            # Stop pulling from receive() as soon as the limit is exceeded: the
            # consumed chunks are replayed to the application, the remaining body
            # keeps flowing live, and analysis is skipped.
            while more_body and not over_limit:
                data_received = await asyncio.wait_for(receive(), asm_config._fast_api_async_body_timeout)
                if data_received is None:
                    more_body = False
                if isinstance(data_received, dict):
                    more_body = data_received.get("more_body", False)
                    chunk = data_received.get("body", b"")
                    replay_parts.append(chunk)
                    if buffered + len(chunk) > body_limit:
                        over_limit = True
                    else:
                        body_parts.append(chunk)
                        buffered += len(chunk)
        except asyncio.TimeoutError:
            pass
        except Exception:
            return receive, None

        if over_limit:
            replay = list(replay_parts)

            async def receive_wrapped_over_limit() -> Optional[dict[str, Any]]:
                if replay:
                    return {"type": "http.request", "body": replay.pop(0), "more_body": True}
                return await receive()

            return receive_wrapped_over_limit, None

        body = b"".join(body_parts)

        async def receive_wrapped(once: list[bool] = [True]) -> Optional[dict[str, Any]]:
            if once[0]:
                once[0] = False
                return {"type": "http.request", "body": body, "more_body": more_body}
            return await receive()

        try:
            with iast_disabled_taint_sources():
                media_type = classify_media_type(headers.get("content-type") or headers.get("Content-Type"))
                if media_type is MediaType.JSON:
                    if body is None or body == b"":
                        req_body = None
                    else:
                        req_body = json.loads(body.decode())
                elif media_type is MediaType.XML:
                    req_body = xmltodict.parse(body)
                elif media_type is MediaType.PLAIN:
                    req_body = None
                else:
                    req_body = parse_form_multipart(body.decode(), headers) or None
            return receive_wrapped, req_body
        except Exception:
            return receive_wrapped, None

    return receive, None


def _asgi_make_block_content(ctx: ExecutionContext[Event], url: str) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    middleware = ctx.get_item("middleware")
    req_span = ctx.get_item("req_span")
    headers = ctx.get_item("headers")
    environ = ctx.get_item("environ")
    if req_span is None:
        raise ValueError("request span not found")
    block_config = get_blocked() or Block_config()
    if block_config.type == "none":
        content = b""
        resp_headers = [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"location", block_config.location.encode()),
        ]
    else:
        content = http_utils._get_blocked_template(block_config.content_type, block_config.block_id).encode("UTF-8")
        resp_headers = [(b"content-type", block_config.content_type.encode())]
    status = block_config.status_code
    try:
        req_span._set_attribute(RESPONSE_HEADERS + ".content-length", str(len(content)))
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
