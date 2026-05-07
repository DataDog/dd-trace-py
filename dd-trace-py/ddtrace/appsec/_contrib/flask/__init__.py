from collections.abc import Mapping
import io
import json
from typing import Any
from typing import Callable
from typing import MutableMapping
from typing import Optional

from ddtrace.appsec._asm_request_context import _call_waf_first
from ddtrace.appsec._asm_request_context import _on_context_ended
from ddtrace.appsec._asm_request_context import _set_headers_and_response
from ddtrace.appsec._asm_request_context import block_request
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import in_asm_context
from ddtrace.appsec._asm_request_context import is_blocked
from ddtrace.appsec._asm_request_context import set_block_request_callable
from ddtrace.appsec._asm_request_context import set_waf_address
from ddtrace.appsec._utils import Block_config
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.trace_utils_base import _get_request_header_user_agent
from ddtrace.contrib.internal.trace_utils_base import _set_url_tag
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal.constants import REQUEST_PATH_PARAMS
from ddtrace.internal.constants import RESPONSE_HEADERS
from ddtrace.internal.core import ExecutionContext
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.internal.utils import http as http_utils
from ddtrace.trace import Span
import ddtrace.vendor.xmltodict as xmltodict


logger = get_logger(__name__)

_BODY_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


def _get_content_length(environ: Mapping[str, Any]) -> Optional[int]:
    content_length = environ.get("CONTENT_LENGTH")
    transfer_encoding = environ.get("HTTP_TRANSFER_ENCODING")

    if transfer_encoding == "chunked" or content_length is None:
        return None

    try:
        return max(0, int(content_length))
    except Exception:
        return 0


def _on_request_span_modifier(
    _ctx: ExecutionContext,
    _flask_config: IntegrationConfig,
    request: Any,
    environ: MutableMapping[str, Any],
    _HAS_JSON_MIXIN: bool,
    _flask_version: object,
    _flask_version_str: str,
    _exception_type: type[BaseException],
) -> Optional[Any]:
    req_body = None
    if asm_config._asm_enabled and request.method in _BODY_METHODS:
        content_type = request.content_type
        wsgi_input = environ.get("wsgi.input", "")

        # Copy wsgi input if not seekable
        seekable = False
        body = b""
        if wsgi_input:
            try:
                seekable = wsgi_input.seekable()
            # expect AttributeError in normal error cases
            except Exception:
                seekable = False
            if not seekable:
                # https://gist.github.com/mitsuhiko/5721547
                # Provide wsgi.input as an end-of-file terminated stream.
                # In that case wsgi.input_terminated is set to True
                # and an app is required to read to the end of the file and disregard CONTENT_LENGTH for reading.
                if environ.get("wsgi.input_terminated"):
                    body = wsgi_input.read()
                else:
                    content_length = _get_content_length(environ)
                    if content_length:
                        body = wsgi_input.read(content_length)
                environ["wsgi.input"] = io.BytesIO(body)

        try:
            if content_type in ("application/json", "text/json"):
                if _HAS_JSON_MIXIN and hasattr(request, "json") and request.json:
                    req_body = request.json
                elif request.data is None or request.data == b"":
                    req_body = None
                else:
                    req_body = json.loads(request.data.decode("UTF-8"))
            elif content_type in ("application/xml", "text/xml"):
                req_body = xmltodict.parse(request.get_data())
            elif hasattr(request, "form"):
                req_body = {k: vs if len(vs) > 1 else vs[0] for k, vs in request.form.to_dict(flat=False).items()}
            else:
                # no raw body
                req_body = None
        except Exception:
            logger.debug("Failed to parse request body", exc_info=True)
        finally:
            # Reset wsgi input to the beginning
            if wsgi_input:
                if seekable:
                    wsgi_input.seek(0)
                else:
                    environ["wsgi.input"] = io.BytesIO(initial_bytes=body)
    return req_body


def _on_flask_blocked_request(span: Span) -> None:
    span._set_attribute(http.STATUS_CODE, "403")
    request = core.find_item("flask_request")
    try:
        base_url = getattr(request, "base_url", None)
        query_string = getattr(request, "query_string", None)
        if base_url and query_string:
            _set_url_tag(core.find_item("flask_config"), span, base_url, query_string)
        if query_string and core.find_item("flask_config").trace_query_string:
            span._set_attribute(http.QUERY_STRING, query_string)
        if request.method is not None:
            span._set_attribute(http.METHOD, request.method)
        user_agent = _get_request_header_user_agent(request.headers)
        if user_agent:
            span._set_attribute(http.USER_AGENT, user_agent)
    except Exception as e:
        logger.warning("Could not set some span tags on blocked request: %s", str(e))


def _on_start_response_blocked(
    ctx: ExecutionContext,
    flask_config: IntegrationConfig,
    response_headers: list[tuple[str, str]],
    status: int,
) -> None:
    trace_utils.set_http_meta(
        ctx["req_span"], flask_config, status_code=status, response_headers=dict(response_headers)
    )


_BlockedViewResponse = tuple[str, int, Mapping[str, str]]


def _make_block_response() -> tuple[str, int, Mapping[str, str]]:
    """Build a blocked response as a tuple (body, status, headers).

    Returning a tuple avoids Flask's error handling (handle_exception,
    handle_http_exception) which would create extra spans without
    fingerprint tags.
    """
    from ddtrace.internal.utils import get_blocked as _get_blocked

    block_config = _get_blocked()
    ctype = block_config.content_type if block_config else "application/json"
    block_id = block_config.block_id if block_config else "(default)"
    status = block_config.status_code if block_config else 403
    if block_config and block_config.type == "none":
        return "", status, {"location": block_config.location}
    return http_utils._get_blocked_template(ctype, block_id), status, {"content-type": ctype}


def _on_wrapped_view(kwargs: Mapping[str, object]) -> Optional[Callable[[], _BlockedViewResponse]]:
    callback_block = None
    # if Appsec is enabled, we can try to block as we have the path parameters at that point
    if asm_config._asm_enabled and in_asm_context():
        logger.debug("asm_context::flask::srb_on_request_param")
        if kwargs:
            set_waf_address(REQUEST_PATH_PARAMS, kwargs)
        call_waf_callback()
        if is_blocked():
            callback_block = _make_block_response
    return callback_block


def _flask_block_request_callable(span: Span) -> None:
    import flask
    from werkzeug.exceptions import abort

    from ddtrace.internal.utils import get_blocked as _get_blocked
    from ddtrace.internal.utils import set_blocked as _set_blocked

    if not _get_blocked():
        _set_blocked()
    core.dispatch("flask.blocked_request_callable", (span,))
    block_config = _get_blocked()
    ctype = block_config.content_type if block_config else "application/json"
    block_id = block_config.block_id if block_config else "(default)"
    status = block_config.status_code if block_config else 403
    if block_config and block_config.type == "none":
        abort(flask.Response(b"", status=status, headers={"location": block_config.location}))
    else:
        abort(flask.Response(http_utils._get_blocked_template(ctype, block_id), content_type=ctype, status=status))


def _on_pre_tracedrequest(ctx: ExecutionContext) -> None:
    import functools

    if asm_config._asm_enabled:
        set_block_request_callable(functools.partial(_flask_block_request_callable, ctx.span))
        if get_blocked():
            block_request()


def _on_block_decided(callback: Callable[[], Any]) -> None:
    if not asm_config._asm_enabled:
        return

    core.on("flask.block.request.content", callback, "block_requested")


def _wsgi_make_block_content(
    ctx: ExecutionContext, construct_url: Callable[[MutableMapping[str, str]], str]
) -> tuple[int, list[tuple[str, str]], bytes]:
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
        resp_headers = [("content-type", "text/plain; charset=utf-8"), ("location", block_config.location)]
    else:
        ctype = block_config.content_type
        content = http_utils._get_blocked_template(ctype, block_config.block_id).encode("UTF-8")
        resp_headers = [("content-type", ctype)]
    status = block_config.status_code
    try:
        req_span._set_attribute(RESPONSE_HEADERS + ".content-length", str(len(content)))
        if ctype is not None:
            req_span._set_attribute(RESPONSE_HEADERS + ".content-type", ctype)
        req_span._set_attribute(http.STATUS_CODE, str(status))
        url = construct_url(environ)
        query_string = environ.get("QUERY_STRING")
        _set_url_tag(middleware._config, req_span, url, query_string)
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
    resp_headers.append(("Content-Length", str(len(content))))
    return status, resp_headers, content


def listen() -> None:
    core.on("flask.request_call_modifier", _on_request_span_modifier, "request_body")
    core.on("flask.blocked_request_callable", _on_flask_blocked_request)
    core.on("flask.start_response.blocked", _on_start_response_blocked)
    core.on("wsgi.block.started", _wsgi_make_block_content, "status_headers_content")

    core.on("flask.finalize_request.post", _set_headers_and_response)
    core.on("flask.wrapped_view", _on_wrapped_view, "callbacks")
    core.on("flask._patched_request", _on_pre_tracedrequest)
    core.on("wsgi.block_decided", _on_block_decided)
    core.on("flask.start_response", _call_waf_first)

    core.on("context.ended.wsgi.__call__", _on_context_ended)
