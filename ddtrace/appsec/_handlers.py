import functools
import io
import json

import xmltodict

from ddtrace.appsec._iast._patch import if_iast_taint_returned_object_for
from ddtrace.appsec._iast._patch import if_iast_taint_yield_tuple_for
from ddtrace.appsec._iast._utils import _is_iast_enabled
from ddtrace.contrib import trace_utils
from ddtrace.internal import core
from ddtrace.internal.constants import HTTP_REQUEST_BLOCKED
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.http import parse_form_multipart
from ddtrace.settings.asm import config as asm_config
from ddtrace.vendor.wrapt import when_imported
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w


log = get_logger(__name__)
_BODY_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


def _get_content_length(environ):
    content_length = environ.get("CONTENT_LENGTH")
    transfer_encoding = environ.get("HTTP_TRANSFER_ENCODING")

    if transfer_encoding == "chunked" or content_length is None:
        return None

    try:
        return max(0, int(content_length))
    except ValueError:
        return 0


# ASGI


async def _on_asgi_request_parse_body(receive, headers):
    if asm_config._asm_enabled:
        data_received = await receive()
        body = data_received.get("body", b"")

        async def receive():
            return data_received

        content_type = headers.get("content-type") or headers.get("Content-Type")
        try:
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
            return receive, req_body
        except BaseException:
            return receive, None

    return receive, None


# FLASK


def _on_request_span_modifier(
    ctx, flask_config, request, environ, _HAS_JSON_MIXIN, flask_version, flask_version_str, exception_type
):
    req_body = None
    if asm_config._asm_enabled and request.method in _BODY_METHODS:
        content_type = request.content_type
        wsgi_input = environ.get("wsgi.input", "")

        # Copy wsgi input if not seekable
        if wsgi_input:
            try:
                seekable = wsgi_input.seekable()
            except AttributeError:
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
                    body = wsgi_input.read(content_length) if content_length else b""
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
                req_body = request.form.to_dict()
            else:
                # no raw body
                req_body = None
        except (
            exception_type,
            AttributeError,
            RuntimeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            xmltodict.expat.ExpatError,
            xmltodict.ParsingInterrupted,
        ):
            log.debug("Failed to parse request body", exc_info=True)
        finally:
            # Reset wsgi input to the beginning
            if wsgi_input:
                if seekable:
                    wsgi_input.seek(0)
                else:
                    environ["wsgi.input"] = io.BytesIO(body)
    return req_body


def _on_request_init(wrapped, instance, args, kwargs):
    wrapped(*args, **kwargs)
    if _is_iast_enabled():
        try:
            from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_source
            from ddtrace.appsec._iast._taint_tracking import OriginType
            from ddtrace.appsec._iast._taint_tracking import taint_pyobject

            # TODO: instance.query_string = ??
            instance.query_string = taint_pyobject(
                pyobject=instance.query_string,
                source_name=OriginType.QUERY,
                source_value=instance.query_string,
                source_origin=OriginType.QUERY,
            )
            instance.path = taint_pyobject(
                pyobject=instance.path,
                source_name=OriginType.PATH,
                source_value=instance.path,
                source_origin=OriginType.PATH,
            )
            _set_metric_iast_instrumented_source(OriginType.PATH)
            _set_metric_iast_instrumented_source(OriginType.QUERY)
        except Exception:
            log.debug("Unexpected exception while tainting pyobject", exc_info=True)


def _on_flask_patch(flask_version):
    if _is_iast_enabled():
        try:
            from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_source
            from ddtrace.appsec._iast._taint_tracking import OriginType

            _w(
                "werkzeug.datastructures",
                "Headers.items",
                functools.partial(if_iast_taint_yield_tuple_for, (OriginType.HEADER_NAME, OriginType.HEADER)),
            )
            _set_metric_iast_instrumented_source(OriginType.HEADER_NAME)
            _set_metric_iast_instrumented_source(OriginType.HEADER)

            _w(
                "werkzeug.datastructures",
                "ImmutableMultiDict.__getitem__",
                functools.partial(if_iast_taint_returned_object_for, OriginType.PARAMETER),
            )
            _set_metric_iast_instrumented_source(OriginType.PARAMETER)

            _w(
                "werkzeug.datastructures",
                "EnvironHeaders.__getitem__",
                functools.partial(if_iast_taint_returned_object_for, OriginType.HEADER),
            )
            _set_metric_iast_instrumented_source(OriginType.HEADER)

            _w("werkzeug.wrappers.request", "Request.__init__", _on_request_init)
            _w(
                "werkzeug.wrappers.request",
                "Request.get_data",
                functools.partial(if_iast_taint_returned_object_for, OriginType.BODY),
            )
            _set_metric_iast_instrumented_source(OriginType.BODY)

            if flask_version < (2, 0, 0):
                _w(
                    "werkzeug._internal",
                    "_DictAccessorProperty.__get__",
                    functools.partial(if_iast_taint_returned_object_for, OriginType.QUERY),
                )
                _set_metric_iast_instrumented_source(OriginType.QUERY)
        except Exception:
            log.debug("Unexpected exception while patch IAST functions", exc_info=True)


def _on_flask_blocked_request(_):
    core.set_item(HTTP_REQUEST_BLOCKED, True)


def _on_django_func_wrapped(fn_args, fn_kwargs, first_arg_expected_type, *_):
    # If IAST is enabled and we're wrapping a Django view call, taint the kwargs (view's
    # path parameters)
    if _is_iast_enabled() and fn_args and isinstance(fn_args[0], first_arg_expected_type):
        from ddtrace.appsec._iast._taint_tracking import OriginType  # noqa: F401
        from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
        from ddtrace.appsec._iast._taint_tracking import taint_pyobject
        from ddtrace.appsec._iast._taint_utils import taint_structure

        http_req = fn_args[0]

        http_req.COOKIES = taint_structure(http_req.COOKIES, OriginType.COOKIE_NAME, OriginType.COOKIE)
        http_req.GET = taint_structure(http_req.GET, OriginType.PARAMETER_NAME, OriginType.PARAMETER)
        http_req.POST = taint_structure(http_req.POST, OriginType.BODY, OriginType.BODY)
        if not is_pyobject_tainted(getattr(http_req, "_body", None)):
            http_req._body = taint_pyobject(
                http_req.body,
                source_name="body",
                source_value=http_req.body,
                source_origin=OriginType.BODY,
            )

        http_req.headers = taint_structure(http_req.headers, OriginType.HEADER_NAME, OriginType.HEADER)
        http_req.path = taint_pyobject(
            http_req.path, source_name="path", source_value=http_req.path, source_origin=OriginType.PATH
        )
        http_req.path_info = taint_pyobject(
            http_req.path_info,
            source_name="path",
            source_value=http_req.path,
            source_origin=OriginType.PATH,
        )
        http_req.environ["PATH_INFO"] = taint_pyobject(
            http_req.environ["PATH_INFO"],
            source_name="path",
            source_value=http_req.path,
            source_origin=OriginType.PATH,
        )
        http_req.META = taint_structure(http_req.META, OriginType.HEADER_NAME, OriginType.HEADER)
        if fn_kwargs:
            try:
                for k, v in fn_kwargs.items():
                    fn_kwargs[k] = taint_pyobject(
                        v, source_name=k, source_value=v, source_origin=OriginType.PATH_PARAMETER
                    )
            except Exception:
                log.debug("IAST: Unexpected exception while tainting path parameters", exc_info=True)


def _on_wsgi_environ(wrapped, _instance, args, kwargs):
    if _is_iast_enabled():
        if not args:
            return wrapped(*args, **kwargs)

        from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_source
        from ddtrace.appsec._iast._taint_tracking import OriginType  # noqa: F401
        from ddtrace.appsec._iast._taint_utils import taint_structure

        _set_metric_iast_instrumented_source(OriginType.HEADER_NAME)
        _set_metric_iast_instrumented_source(OriginType.HEADER)
        # we instrument those sources on _on_django_func_wrapped
        _set_metric_iast_instrumented_source(OriginType.PATH_PARAMETER)
        _set_metric_iast_instrumented_source(OriginType.PATH)
        _set_metric_iast_instrumented_source(OriginType.COOKIE)
        _set_metric_iast_instrumented_source(OriginType.COOKIE_NAME)
        _set_metric_iast_instrumented_source(OriginType.PARAMETER)
        _set_metric_iast_instrumented_source(OriginType.PARAMETER_NAME)
        _set_metric_iast_instrumented_source(OriginType.BODY)

        return wrapped(*((taint_structure(args[0], OriginType.HEADER_NAME, OriginType.HEADER),) + args[1:]), **kwargs)

    return wrapped(*args, **kwargs)


def _on_django_patch():
    try:
        from ddtrace.appsec._iast._taint_tracking import OriginType  # noqa: F401

        when_imported("django.http.request")(
            lambda m: trace_utils.wrap(
                m,
                "QueryDict.__getitem__",
                functools.partial(if_iast_taint_returned_object_for, OriginType.PARAMETER),
            )
        )
    except Exception:
        log.debug("Unexpected exception while patch IAST functions", exc_info=True)


def listen():
    core.on("flask.request_call_modifier", _on_request_span_modifier, "request_body")
    core.on("flask.request_init", _on_request_init)
    core.on("flask.blocked_request_callable", _on_flask_blocked_request)


core.on("django.func.wrapped", _on_django_func_wrapped)
core.on("django.wsgi_environ", _on_wsgi_environ, "wrapped_result")
core.on("django.patch", _on_django_patch)
core.on("flask.patch", _on_flask_patch)

core.on("asgi.request.parse.body", _on_asgi_request_parse_body, "await_receive_and_body")
