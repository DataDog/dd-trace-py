import functools
import json

from six import BytesIO
import xmltodict

from ddtrace import config
from ddtrace.appsec._constants import IAST
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.appsec._constants import WAF_CONTEXT_NAMES
from ddtrace.appsec.iast._patch import if_iast_taint_returned_object_for
from ddtrace.appsec.iast._patch import if_iast_taint_yield_tuple_for
from ddtrace.appsec.iast._util import _is_iast_enabled
from ddtrace.internal import core
from ddtrace.internal.constants import HTTP_REQUEST_PATH
from ddtrace.internal.constants import HTTP_REQUEST_QUERY
from ddtrace.internal.logger import get_logger


try:
    from json import JSONDecodeError
except ImportError:
    # handling python 2.X import error
    JSONDecodeError = ValueError  # type: ignore

log = get_logger(__name__)
_BODY_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


def _on_wrapped_view(kwargs):
    from ddtrace.appsec import _asm_request_context

    return_value = [None, None]
    # if Appsec is enabled, we can try to block as we have the path parameters at that point
    if config._appsec_enabled and _asm_request_context.in_context():
        log.debug("Flask WAF call for Suspicious Request Blocking on request")
        if kwargs:
            _asm_request_context.set_waf_address(SPAN_DATA_NAMES.REQUEST_PATH_PARAMS, kwargs)
        _asm_request_context.call_waf_callback()
        if _asm_request_context.is_blocked():
            callback_block = _asm_request_context.get_value(_asm_request_context._CALLBACKS, "flask_block")
            return_value[0] = callback_block

    # If IAST is enabled, taint the Flask function kwargs (path parameters)
    if _is_iast_enabled() and kwargs:
        from ddtrace.appsec.iast._input_info import Input_info
        from ddtrace.appsec.iast._taint_tracking import taint_pyobject

        _kwargs = {}
        for k, v in kwargs.items():
            _kwargs[k] = taint_pyobject(v, Input_info(k, v, IAST.HTTP_REQUEST_PATH_PARAMETER))
        return_value[1] = _kwargs
    return return_value


def _on_set_request_tags(request, span, flask_config):
    if _is_iast_enabled():
        from ddtrace.appsec.iast._taint_utils import LazyTaintDict

        request.cookies = LazyTaintDict(
            request.cookies,
            origins=(IAST.HTTP_REQUEST_COOKIE_NAME, IAST.HTTP_REQUEST_COOKIE_VALUE),
            override_pyobject_tainted=True,
        )


def _on_pre_tracedrequest(flask_config, block_request_callable, current_span, req_span):
    if config._appsec_enabled:
        from ddtrace.appsec import _asm_request_context

        _asm_request_context.set_block_request_callable(functools.partial(block_request_callable, current_span))
        if core.get_item(WAF_CONTEXT_NAMES.BLOCKED):
            _asm_request_context.block_request()


def _on_post_finalizerequest(rv):
    if config._api_security_enabled and config._appsec_enabled and getattr(rv, "is_sequence", False):
        from ddtrace.appsec import _asm_request_context

        # start_response was not called yet, set the HTTP response headers earlier
        _asm_request_context.set_headers_response(list(rv.headers))
        _asm_request_context.set_body_response(rv.response)


def _on_request_span_modifier(request, environ, _HAS_JSON_MIXIN):
    req_body = None
    if config._appsec_enabled and request.method in _BODY_METHODS:
        content_type = request.content_type
        wsgi_input = environ.get("wsgi.input", "")

        # Copy wsgi input if not seekable
        if wsgi_input:
            try:
                seekable = wsgi_input.seekable()
            except AttributeError:
                seekable = False
            if not seekable:
                content_length = int(environ.get("CONTENT_LENGTH", 0))
                body = wsgi_input.read(content_length) if content_length else wsgi_input.read()
                environ["wsgi.input"] = BytesIO(body)

        try:
            if content_type == "application/json" or content_type == "text/json":
                if _HAS_JSON_MIXIN and hasattr(request, "json") and request.json:
                    req_body = request.json
                else:
                    req_body = json.loads(request.data.decode("UTF-8"))
            elif content_type in ("application/xml", "text/xml"):
                req_body = xmltodict.parse(request.get_data())
            elif hasattr(request, "form"):
                req_body = request.form.to_dict()
            else:
                # no raw body
                req_body = None
        except (
            AttributeError,
            RuntimeError,
            TypeError,
            ValueError,
            JSONDecodeError,
            xmltodict.expat.ExpatError,
            xmltodict.ParsingInterrupted,
        ):
            log.warning("Failed to parse werkzeug request body", exc_info=True)
        finally:
            # Reset wsgi input to the beginning
            if wsgi_input:
                if seekable:
                    wsgi_input.seek(0)
                else:
                    environ["wsgi.input"] = BytesIO(body)
    return req_body


def _on_start_response():
    from ddtrace.appsec import _asm_request_context

    log.debug("Flask WAF call for Suspicious Request Blocking on response")
    _asm_request_context.call_waf_callback()
    return _asm_request_context.get_headers().get("Accept", "").lower()


def _on_block_decided(callback):
    from ddtrace.appsec import _asm_request_context

    _asm_request_context.set_value(_asm_request_context._CALLBACKS, "flask_block", callback)


def _on_request_init(instance):
    if _is_iast_enabled():
        try:
            from ddtrace.appsec.iast._input_info import Input_info
            from ddtrace.appsec.iast._taint_tracking import taint_pyobject

            taint_pyobject(
                instance.query_string,
                Input_info(HTTP_REQUEST_QUERY, instance.query_string, HTTP_REQUEST_QUERY),
            )
            taint_pyobject(instance.path, Input_info(HTTP_REQUEST_PATH, instance.path, HTTP_REQUEST_PATH))
        except Exception:
            log.debug("Unexpected exception while tainting pyobject", exc_info=True)


def _on_werkzeug(*args):
    if isinstance(args[0], tuple):
        return if_iast_taint_yield_tuple_for(*args)
    return if_iast_taint_returned_object_for(*args)


def listen():
    core.on("wsgi.block_decided", _on_block_decided)
    core.on("flask.start_response", _on_start_response)
    core.on("flask.wrapped_view", _on_wrapped_view)
    core.on("flask.set_request_tags", _on_set_request_tags)
    core.on("flask.traced_request.pre", _on_pre_tracedrequest)
    core.on("flask.finalize_request.post", _on_post_finalizerequest)
    core.on("flask.request_span_modifier", _on_request_span_modifier)
    core.on("flask.request_init", _on_request_init)


core.on("flask.werkzeug.datastructures.Headers.items", _on_werkzeug)
core.on("flask.werkzeug.datastructures.EnvironHeaders.__getitem__", _on_werkzeug)
core.on("flask.werkzeug.datastructures.ImmutableMultiDict.__getitem__", _on_werkzeug)
core.on("flask.werkzeug.wrappers.request.Request.get_data", _on_werkzeug)
core.on("flask.werkzeug._internal._DictAccessorProperty.__get__", _on_werkzeug)
