from collections.abc import Callable
import json
from typing import Any

from ddtrace.appsec._asm_request_context import _on_context_ended
from ddtrace.appsec._asm_request_context import _use_html
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import get_headers
from ddtrace.appsec._asm_request_context import set_waf_address
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.appsec._utils import Block_config
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.asm import config as asm_config


logger = get_logger(__name__)


def tornado_block(_integration: str, handler: Any, block: Block_config) -> Any:
    setattr(handler, "__dd_appsec_blocked", True)
    handler.clear()
    handler.set_status(block.status_code)
    handler._transforms = ()
    if 300 <= block.status_code < 400 and block.location:
        return handler.redirect(block.location, status=block.status_code)
    if block.content_type == "auto":
        content_type = "text/html" if _use_html(handler.headers) else "application/json"
    else:
        content_type = block.content_type
    handler.set_header("Content-Type", content_type)
    from ddtrace.internal.utils.http import _get_blocked_template

    set_waf_address(SPAN_DATA_NAMES.RESPONSE_STATUS, str(block.status_code))
    set_waf_address(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES, getattr(handler, "_headers", None))
    return handler.finish(_get_blocked_template(content_type, block.block_id))


def tornado_call_waf_first(integration: str, handler: Any) -> None:
    if not asm_config._asm_enabled:
        return
    info = f"{integration}::srb_on_request"
    logger.debug(info)
    call_waf_callback()
    if block := get_blocked():
        tornado_block(integration, handler, block)
        return
    # adding body request support
    handler.request._parse_body()
    request_headers = get_headers() or {}
    parsed_body = handler.request.body_arguments
    if parsed_body:
        parsed_body = {k: v[0] if len(v) == 1 else list(v) for k, v in parsed_body.items()}
    else:
        _body: bytes = handler.request.body
        try:
            if "json" in request_headers.get("content-type", ""):
                parsed_body = json.loads(_body)
        except BaseException:
            pass  # nosec
        try:
            if not parsed_body and "xml" in request_headers.get("content-type", ""):
                import ddtrace.vendor.xmltodict as xmltodict

                parsed_body = xmltodict.parse(_body)
        except BaseException:
            pass  # nosec
    if parsed_body:
        set_waf_address(SPAN_DATA_NAMES.REQUEST_BODY, parsed_body)
        call_waf_callback()
        if block := get_blocked():
            tornado_block(integration, handler, block)

    return None


def _tornado_parse_body(handler: Any) -> Callable[[], Any]:
    response_body = b"".join(handler._write_buffer)

    def lambda_function() -> Any:
        try:
            return json.loads(response_body)
        except BaseException:
            return None

    return lambda_function


def tornado_call_waf_response(integration: str, handler: Any) -> None:
    if not asm_config._asm_enabled:
        return
    if getattr(handler, "__dd_appsec_blocked", False):
        return
    info = f"{integration}::srb_on_response"
    logger.debug(info)
    status_code = getattr(handler, "_status_code", None)
    if isinstance(status_code, int):
        status_code = str(status_code)
    response_headers = getattr(handler, "_headers", None)
    set_waf_address(SPAN_DATA_NAMES.RESPONSE_STATUS, status_code)
    set_waf_address(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES, response_headers)
    call_waf_callback()
    if block := get_blocked():
        raise BlockingException(block)
    set_waf_address(SPAN_DATA_NAMES.RESPONSE_BODY, _tornado_parse_body(handler))
    call_waf_callback()
    if block := get_blocked():
        tornado_block(integration, handler, block)


def listen() -> None:
    core.on("tornado.start_request", tornado_call_waf_first, "tornado_future")
    core.on("tornado.block_request", tornado_block, "tornado_future")
    core.on("tornado.send_response", tornado_call_waf_response)
    core.on("context.ended.request.tornado", _on_context_ended)
