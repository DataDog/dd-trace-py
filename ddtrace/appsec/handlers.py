import functools
import json

from six import BytesIO
import xmltodict

from ddtrace import config
import ddtrace.appsec._constants as _constants
from ddtrace.appsec.iast._metrics import _set_metric_iast_instrumented_source
from ddtrace.appsec.iast._patch import if_iast_taint_returned_object_for
from ddtrace.appsec.iast._patch import if_iast_taint_yield_tuple_for
from ddtrace.appsec.iast._util import _is_iast_enabled
from ddtrace.contrib import trace_utils
from ddtrace.contrib.trace_utils import _get_request_header_user_agent
from ddtrace.contrib.trace_utils import _set_url_tag
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal.constants import RESPONSE_HEADERS
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import http as http_utils
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w
from ddtrace.vendor.wrapt.importer import when_imported


try:
    from json import JSONDecodeError
except ImportError:
    # handling python 2.X import error
    JSONDecodeError = ValueError  # type: ignore

log = get_logger(__name__)
_BODY_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


def _on_request_span_modifier(request, environ, _HAS_JSON_MIXIN, exception_type):
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
            exception_type,
            AttributeError,
            RuntimeError,
            TypeError,
            ValueError,
            JSONDecodeError,
            xmltodict.expat.ExpatError,
            xmltodict.ParsingInterrupted,
        ):
            log.warning("Failed to parse request body", exc_info=True)
        finally:
            # Reset wsgi input to the beginning
            if wsgi_input:
                if seekable:
                    wsgi_input.seek(0)
                else:
                    environ["wsgi.input"] = BytesIO(body)
    return req_body


def _on_request_init(wrapped, instance, args, kwargs):
    wrapped(*args, **kwargs)
    if _is_iast_enabled():
        try:
            from ddtrace.appsec.iast._taint_tracking import OriginType
            from ddtrace.appsec.iast._taint_tracking import taint_pyobject

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
            from ddtrace.appsec.iast._taint_tracking import OriginType

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


def _on_django_func_wrapped(fn_args, fn_kwargs, first_arg_expected_type):
    # If IAST is enabled and we're wrapping a Django view call, taint the kwargs (view's
    # path parameters)
    if _is_iast_enabled() and fn_args and isinstance(fn_args[0], first_arg_expected_type):
        from ddtrace.appsec.iast._taint_tracking import OriginType  # noqa: F401
        from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted
        from ddtrace.appsec.iast._taint_tracking import taint_pyobject
        from ddtrace.appsec.iast._taint_utils import LazyTaintDict

        http_req = fn_args[0]

        if not isinstance(http_req.COOKIES, LazyTaintDict):
            http_req.COOKIES = LazyTaintDict(http_req.COOKIES, origins=(OriginType.COOKIE_NAME, OriginType.COOKIE))
        if not isinstance(http_req.GET, LazyTaintDict):
            http_req.GET = LazyTaintDict(http_req.GET, origins=(OriginType.PARAMETER_NAME, OriginType.PARAMETER))
        if not isinstance(http_req.POST, LazyTaintDict):
            http_req.POST = LazyTaintDict(http_req.POST, origins=(OriginType.BODY, OriginType.BODY))
        if not is_pyobject_tainted(getattr(http_req, "_body", None)):
            http_req._body = taint_pyobject(
                http_req.body,
                source_name="body",
                source_value=http_req.body,
                source_origin=OriginType.BODY,
            )

        if not isinstance(http_req.META, LazyTaintDict):
            http_req.META = LazyTaintDict(http_req.META, origins=(OriginType.HEADER_NAME, OriginType.HEADER))
        if not isinstance(http_req.headers, LazyTaintDict):
            http_req.headers = LazyTaintDict(http_req.headers, origins=(OriginType.HEADER_NAME, OriginType.HEADER))
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

        from ddtrace.appsec.iast._taint_tracking import OriginType  # noqa: F401
        from ddtrace.appsec.iast._taint_utils import LazyTaintDict

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

        return wrapped(
            *((LazyTaintDict(args[0], origins=(OriginType.HEADER_NAME, OriginType.HEADER)),) + args[1:]), **kwargs
        )

    return wrapped(*args, **kwargs)


def _on_django_patch():
    try:
        from ddtrace.appsec.iast._taint_tracking import OriginType  # noqa: F401

        when_imported("django.http.request")(
            lambda m: trace_utils.wrap(
                m,
                "QueryDict.__getitem__",
                functools.partial(if_iast_taint_returned_object_for, OriginType.PARAMETER),
            )
        )
    except Exception:
        log.debug("Unexpected exception while patch IAST functions", exc_info=True)


def _on_flask_blocked_request(span):
    core.set_item(_constants.WAF_CONTEXT_NAMES.BLOCKED, _constants.WAF_ACTIONS.DEFAULT_PARAMETERS)
    span.set_tag_str(http.STATUS_CODE, "403")
    request = core.get_item("flask_request")
    try:
        base_url = getattr(request, "base_url", None)
        query_string = getattr(request, "query_string", None)
        if base_url and query_string:
            _set_url_tag(core.get_item("flask_config"), span, base_url, query_string)
        if query_string and core.get_item("flask_config").trace_query_string:
            span.set_tag_str(http.QUERY_STRING, query_string)
        if request.method is not None:
            span.set_tag_str(http.METHOD, request.method)
        user_agent = _get_request_header_user_agent(request.headers)
        if user_agent:
            span.set_tag_str(http.USER_AGENT, user_agent)
    except Exception as e:
        log.warning("Could not set some span tags on blocked request: %s", str(e))  # noqa: G200


def _make_block_content(ctx, construct_url):
    middleware = ctx.get_item("middleware")
    req_span = ctx.get_item("req_span")
    headers = ctx.get_item("headers")
    environ = ctx.get_item("environ")
    if req_span is None:
        raise ValueError("request span not found")
    block_config = core.get_item(_constants.WAF_CONTEXT_NAMES.BLOCKED, span=req_span)
    if block_config.get("type", "auto") == "auto":
        ctype = "text/html" if "text/html" in headers.get("Accept", "").lower() else "text/json"
    else:
        ctype = "text/" + block_config["type"]
    content = http_utils._get_blocked_template(ctype).encode("UTF-8")
    status = block_config.get("status_code", 403)
    try:
        req_span.set_tag_str(RESPONSE_HEADERS + ".content-length", str(len(content)))
        req_span.set_tag_str(RESPONSE_HEADERS + ".content-type", ctype)
        req_span.set_tag_str(http.STATUS_CODE, str(status))
        url = construct_url(environ)
        query_string = environ.get("QUERY_STRING")
        _set_url_tag(middleware._config, req_span, url, query_string)
        if query_string and middleware._config.trace_query_string:
            req_span.set_tag_str(http.QUERY_STRING, query_string)
        method = environ.get("REQUEST_METHOD")
        if method:
            req_span.set_tag_str(http.METHOD, method)
        user_agent = _get_request_header_user_agent(headers, headers_are_case_sensitive=True)
        if user_agent:
            req_span.set_tag_str(http.USER_AGENT, user_agent)
    except Exception as e:
        log.warning("Could not set some span tags on blocked request: %s", str(e))  # noqa: G200

    return status, ctype, content


def listen():
    core.on("flask.request_span_modifier", _on_request_span_modifier)
    core.on("flask.blocked_request_callable", _on_flask_blocked_request)
    core.on("django.func.wrapped", _on_django_func_wrapped)
    core.on("django.wsgi_environ", _on_wsgi_environ)
    core.on("django.patch", _on_django_patch)
    core.on("flask.patch", _on_flask_patch)
    core.on("wsgi.block.started", _make_block_content)
