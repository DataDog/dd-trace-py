from collections.abc import MutableMapping
import functools

from wrapt import when_imported
from wrapt import wrap_function_wrapper as _w

from ddtrace.appsec._iast._iast_request_context import get_iast_stacktrace_reported
from ddtrace.appsec._iast._iast_request_context import set_iast_stacktrace_reported
from ddtrace.appsec._iast._logs import iast_instrumentation_wrapt_debug_log
from ddtrace.appsec._iast._logs import iast_propagation_listener_log_log
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_source
from ddtrace.appsec._iast._patch import _iast_instrument_starlette_request
from ddtrace.appsec._iast._patch import _iast_instrument_starlette_request_body
from ddtrace.appsec._iast._patch import _iast_instrument_starlette_url
from ddtrace.appsec._iast._patch import _patched_dictionary
from ddtrace.appsec._iast._patch import try_wrap_function_wrapper
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import origin_to_str
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_utils import taint_structure
from ddtrace.appsec._iast.secure_marks.sanitizers import command_quote_sanitizer
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


MessageMapContainer = None
try:
    from google._upb._message import MessageMapContainer  # type: ignore[no-redef]
except ImportError:
    pass


log = get_logger(__name__)


def _on_request_init(wrapped, instance, args, kwargs):
    wrapped(*args, **kwargs)
    if asm_config._iast_enabled and asm_config.is_iast_request_enabled:
        try:
            instance.query_string = taint_pyobject(
                pyobject=instance.query_string,
                source_name=origin_to_str(OriginType.QUERY),
                source_value=instance.query_string,
                source_origin=OriginType.QUERY,
            )
            instance.path = taint_pyobject(
                pyobject=instance.path,
                source_name=origin_to_str(OriginType.PATH),
                source_value=instance.path,
                source_origin=OriginType.PATH,
            )
        except Exception:
            iast_propagation_listener_log_log("Unexpected exception while tainting pyobject", exc_info=True)


def _on_flask_patch(flask_version):
    """Handle Flask framework patch event.

    Args:
        flask_version: The version tuple of Flask being patched
    """
    if asm_config._iast_enabled:
        try:
            try_wrap_function_wrapper(
                "werkzeug.datastructures",
                "Headers.items",
                functools.partial(if_iast_taint_yield_tuple_for, (OriginType.HEADER_NAME, OriginType.HEADER)),
            )

            try_wrap_function_wrapper(
                "werkzeug.datastructures",
                "EnvironHeaders.__getitem__",
                functools.partial(if_iast_taint_returned_object_for, OriginType.HEADER),
            )
            # Since werkzeug 3.1.0 get doesn't call to __getitem__
            try_wrap_function_wrapper(
                "werkzeug.datastructures",
                "EnvironHeaders.get",
                functools.partial(if_iast_taint_returned_object_for, OriginType.HEADER),
            )
            _set_metric_iast_instrumented_source(OriginType.HEADER_NAME)
            _set_metric_iast_instrumented_source(OriginType.HEADER)

            try_wrap_function_wrapper(
                "werkzeug.datastructures",
                "ImmutableMultiDict.__getitem__",
                functools.partial(if_iast_taint_returned_object_for, OriginType.PARAMETER),
            )
            _set_metric_iast_instrumented_source(OriginType.PARAMETER)

            if flask_version >= (2, 0, 0):
                # instance.query_string: raising an error on werkzeug/_internal.py "AttributeError: read only property"
                try_wrap_function_wrapper("werkzeug.wrappers.request", "Request.__init__", _on_request_init)

            _set_metric_iast_instrumented_source(OriginType.PATH)
            _set_metric_iast_instrumented_source(OriginType.QUERY)

            try_wrap_function_wrapper(
                "werkzeug.wrappers.request",
                "Request.get_data",
                functools.partial(_patched_dictionary, OriginType.BODY, OriginType.BODY),
            )
            try_wrap_function_wrapper(
                "werkzeug.wrappers.request",
                "Request.get_json",
                functools.partial(_patched_dictionary, OriginType.BODY, OriginType.BODY),
            )

            _set_metric_iast_instrumented_source(OriginType.BODY)

            if flask_version < (2, 0, 0):
                _w(
                    "werkzeug._internal",
                    "_DictAccessorProperty.__get__",
                    functools.partial(if_iast_taint_returned_object_for, OriginType.QUERY),
                )
                _set_metric_iast_instrumented_source(OriginType.QUERY)

            # Instrumented on _ddtrace.appsec._asm_request_context._on_wrapped_view
            _set_metric_iast_instrumented_source(OriginType.PATH_PARAMETER)

            # Instrumented on _on_set_request_tags_iast
            _set_metric_iast_instrumented_source(OriginType.COOKIE_NAME)
            _set_metric_iast_instrumented_source(OriginType.COOKIE)
            _set_metric_iast_instrumented_source(OriginType.PARAMETER_NAME)

            iast_instrumentation_wrapt_debug_log("Patching flask correctly")
        except Exception:
            iast_instrumentation_wrapt_debug_log("Unexpected exception while patching Flask", exc_info=True)


def _iast_on_wrapped_view(kwargs):
    # If IAST is enabled, taint the Flask function kwargs (path parameters)
    if kwargs and asm_config._iast_enabled:
        if not asm_config.is_iast_request_enabled:
            return kwargs

        _kwargs = {}
        for k, v in kwargs.items():
            _kwargs[k] = taint_pyobject(
                pyobject=v, source_name=k, source_value=v, source_origin=OriginType.PATH_PARAMETER
            )
        return _kwargs
    return kwargs


def _on_wsgi_environ(wrapped, _instance, args, kwargs):
    if asm_config._iast_enabled and args and asm_config.is_iast_request_enabled:
        return wrapped(*((taint_structure(args[0], OriginType.HEADER_NAME, OriginType.HEADER),) + args[1:]), **kwargs)

    return wrapped(*args, **kwargs)


def _on_django_patch():
    """Handle Django framework patch event."""
    if asm_config._iast_enabled:
        try:
            when_imported("django.http.request")(
                lambda m: try_wrap_function_wrapper(
                    m,
                    "QueryDict.__getitem__",
                    functools.partial(if_iast_taint_returned_object_for, OriginType.PARAMETER),
                )
            )
            try_wrap_function_wrapper("django.utils.shlex", "quote", command_quote_sanitizer)
            # we instrument those sources on _on_django_func_wrapped
            _set_metric_iast_instrumented_source(OriginType.HEADER_NAME)
            _set_metric_iast_instrumented_source(OriginType.HEADER)
            _set_metric_iast_instrumented_source(OriginType.PATH_PARAMETER)
            _set_metric_iast_instrumented_source(OriginType.PATH)
            _set_metric_iast_instrumented_source(OriginType.COOKIE)
            _set_metric_iast_instrumented_source(OriginType.COOKIE_NAME)
            _set_metric_iast_instrumented_source(OriginType.PARAMETER)
            _set_metric_iast_instrumented_source(OriginType.PARAMETER_NAME)
            _set_metric_iast_instrumented_source(OriginType.BODY)
            iast_instrumentation_wrapt_debug_log("Patching Django correctly")
        except Exception:
            iast_instrumentation_wrapt_debug_log("Unexpected exception while patching Django", exc_info=True)


def _on_django_func_wrapped(fn_args, fn_kwargs, first_arg_expected_type, *_):
    # If IAST is enabled, and we're wrapping a Django view call, taint the kwargs (view's
    # path parameters)
    if asm_config._iast_enabled and fn_args and isinstance(fn_args[0], first_arg_expected_type):
        if not asm_config.is_iast_request_enabled:
            return

        http_req = fn_args[0]

        http_req.COOKIES = taint_structure(http_req.COOKIES, OriginType.COOKIE_NAME, OriginType.COOKIE)
        if (
            getattr(http_req, "_body", None) is not None
            and len(getattr(http_req, "_body", None)) > 0
            and not is_pyobject_tainted(getattr(http_req, "_body", None))
        ):
            try:
                http_req._body = taint_pyobject(
                    http_req._body,
                    source_name=origin_to_str(OriginType.BODY),
                    source_value=http_req._body,
                    source_origin=OriginType.BODY,
                )
            except AttributeError:
                log.debug("IAST can't set attribute http_req._body", exc_info=True)
        elif (
            getattr(http_req, "body", None) is not None
            and len(getattr(http_req, "body", None)) > 0
            and not is_pyobject_tainted(getattr(http_req, "body", None))
        ):
            try:
                http_req.body = taint_pyobject(
                    http_req.body,
                    source_name=origin_to_str(OriginType.BODY),
                    source_value=http_req.body,
                    source_origin=OriginType.BODY,
                )
            except AttributeError:
                iast_propagation_listener_log_log("IAST can't set attribute http_req.body", exc_info=True)

        http_req.GET = taint_structure(http_req.GET, OriginType.PARAMETER_NAME, OriginType.PARAMETER)
        http_req.POST = taint_structure(http_req.POST, OriginType.PARAMETER_NAME, OriginType.BODY)
        http_req.headers = taint_structure(http_req.headers, OriginType.HEADER_NAME, OriginType.HEADER)
        http_req.path = taint_pyobject(
            http_req.path, source_name="path", source_value=http_req.path, source_origin=OriginType.PATH
        )
        http_req.path_info = taint_pyobject(
            http_req.path_info,
            source_name=origin_to_str(OriginType.PATH),
            source_value=http_req.path,
            source_origin=OriginType.PATH,
        )
        http_req.environ["PATH_INFO"] = taint_pyobject(
            http_req.environ["PATH_INFO"],
            source_name=origin_to_str(OriginType.PATH),
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
                iast_propagation_listener_log_log("Unexpected exception while tainting path parameters", exc_info=True)


def _custom_protobuf_getattribute(self, name):
    ret = type(self).__saved_getattr(self, name)
    if isinstance(ret, (str, bytes, bytearray)):
        ret = taint_pyobject(
            pyobject=ret,
            source_name=OriginType.GRPC_BODY,
            source_value=ret,
            source_origin=OriginType.GRPC_BODY,
        )
    elif MessageMapContainer is not None and isinstance(ret, MutableMapping):
        if isinstance(ret, MessageMapContainer) and len(ret):
            # Patch the message-values class
            first_key = next(iter(ret))
            value_type = type(ret[first_key])
            _patch_protobuf_class(value_type)
        else:
            ret = taint_structure(ret, OriginType.GRPC_BODY, OriginType.GRPC_BODY)

    return ret


_custom_protobuf_getattribute.__datadog_custom = True  # type: ignore[attr-defined]


# Used to replace the Protobuf message class "getattribute" with a custom one that taints the return
# of the original __getattribute__ method
def _patch_protobuf_class(cls):
    getattr_method = getattr(cls, "__getattribute__")
    if not getattr_method:
        return

    if not hasattr(getattr_method, "__datadog_custom"):
        try:
            # Replace the class __getattribute__ method with our custom one
            # (replacement is done at the class level because it would incur on a recursive loop with the instance)
            cls.__saved_getattr = getattr_method
            cls.__getattribute__ = _custom_protobuf_getattribute
        except TypeError:
            # Avoid failing on Python 3.12 while patching immutable types
            pass


def _on_grpc_response(message):
    if asm_config._iast_enabled:
        msg_cls = type(message)
        _patch_protobuf_class(msg_cls)


def if_iast_taint_yield_tuple_for(origins, wrapped, instance, args, kwargs):
    if asm_config._iast_enabled and asm_config.is_iast_request_enabled:
        try:
            for key, value in wrapped(*args, **kwargs):
                new_key = taint_pyobject(pyobject=key, source_name=key, source_value=key, source_origin=origins[0])
                new_value = taint_pyobject(
                    pyobject=value, source_name=key, source_value=value, source_origin=origins[1]
                )
                yield new_key, new_value
        except Exception:
            iast_propagation_listener_log_log("Unexpected exception while tainting pyobject", exc_info=True)
    else:
        for key, value in wrapped(*args, **kwargs):
            yield key, value


def if_iast_taint_returned_object_for(origin, wrapped, instance, args, kwargs):
    value = wrapped(*args, **kwargs)
    if asm_config._iast_enabled and asm_config.is_iast_request_enabled:
        try:
            if not is_pyobject_tainted(value):
                name = str(args[0]) if len(args) else "http.request.body"
                if origin == OriginType.HEADER and name.lower() in ["cookie", "cookies"]:
                    origin = OriginType.COOKIE
                return taint_pyobject(pyobject=value, source_name=name, source_value=value, source_origin=origin)
        except Exception:
            iast_propagation_listener_log_log("Unexpected exception while tainting pyobject", exc_info=True)
    return value


def if_iast_taint_starlette_datastructures(origin, wrapped, instance, args, kwargs):
    value = wrapped(*args, **kwargs)
    if asm_config._iast_enabled and asm_config.is_iast_request_enabled:
        try:
            res = []
            for element in value:
                if not is_pyobject_tainted(element):
                    res.append(
                        taint_pyobject(
                            pyobject=element,
                            source_name=element,
                            source_value=element,
                            source_origin=origin,
                        )
                    )
                else:
                    res.append(element)
            return res
        except Exception:
            iast_propagation_listener_log_log("Unexpected exception while tainting pyobject", exc_info=True)
    return value


def _on_iast_fastapi_patch():
    # Cookies sources
    try_wrap_function_wrapper(
        "starlette.requests",
        "cookie_parser",
        functools.partial(_patched_dictionary, OriginType.COOKIE_NAME, OriginType.COOKIE),
    )
    _set_metric_iast_instrumented_source(OriginType.COOKIE)
    _set_metric_iast_instrumented_source(OriginType.COOKIE_NAME)

    # Parameter sources
    try_wrap_function_wrapper(
        "starlette.datastructures",
        "QueryParams.__getitem__",
        functools.partial(if_iast_taint_returned_object_for, OriginType.PARAMETER),
    )
    try_wrap_function_wrapper(
        "starlette.datastructures",
        "QueryParams.get",
        functools.partial(if_iast_taint_returned_object_for, OriginType.PARAMETER),
    )
    _set_metric_iast_instrumented_source(OriginType.PARAMETER)

    try_wrap_function_wrapper(
        "starlette.datastructures",
        "QueryParams.keys",
        functools.partial(if_iast_taint_starlette_datastructures, OriginType.PARAMETER_NAME),
    )
    _set_metric_iast_instrumented_source(OriginType.PARAMETER_NAME)

    # Header sources
    try_wrap_function_wrapper(
        "starlette.datastructures",
        "Headers.__getitem__",
        functools.partial(if_iast_taint_returned_object_for, OriginType.HEADER),
    )
    try_wrap_function_wrapper(
        "starlette.datastructures",
        "Headers.get",
        functools.partial(if_iast_taint_returned_object_for, OriginType.HEADER),
    )
    _set_metric_iast_instrumented_source(OriginType.HEADER)

    try_wrap_function_wrapper(
        "starlette.datastructures",
        "Headers.keys",
        functools.partial(if_iast_taint_starlette_datastructures, OriginType.HEADER_NAME),
    )
    _set_metric_iast_instrumented_source(OriginType.HEADER_NAME)

    # Path source
    try_wrap_function_wrapper("starlette.datastructures", "URL.__init__", _iast_instrument_starlette_url)
    _set_metric_iast_instrumented_source(OriginType.PATH)

    # Body source
    try_wrap_function_wrapper("starlette.requests", "Request.__init__", _iast_instrument_starlette_request)
    try_wrap_function_wrapper("starlette.requests", "Request.body", _iast_instrument_starlette_request_body)
    try_wrap_function_wrapper(
        "starlette.datastructures",
        "FormData.__getitem__",
        functools.partial(if_iast_taint_returned_object_for, OriginType.BODY),
    )
    try_wrap_function_wrapper(
        "starlette.datastructures",
        "FormData.get",
        functools.partial(if_iast_taint_returned_object_for, OriginType.BODY),
    )
    try_wrap_function_wrapper(
        "starlette.datastructures",
        "FormData.keys",
        functools.partial(if_iast_taint_starlette_datastructures, OriginType.PARAMETER_NAME),
    )

    _set_metric_iast_instrumented_source(OriginType.BODY)

    # Instrumented on _iast_starlette_scope_taint
    _set_metric_iast_instrumented_source(OriginType.PATH_PARAMETER)


def _on_pre_tracedrequest_iast(ctx):
    current_span = ctx.span
    _on_set_request_tags_iast(ctx.get_item("flask_request"), current_span, ctx.get_item("flask_config"))


def _on_set_request_tags_iast(request, span, flask_config):
    if asm_config._iast_enabled and asm_config.is_iast_request_enabled:
        request.cookies = taint_structure(
            request.cookies,
            OriginType.COOKIE_NAME,
            OriginType.COOKIE,
            override_pyobject_tainted=True,
        )

        request.args = taint_structure(
            request.args,
            OriginType.PARAMETER_NAME,
            OriginType.PARAMETER,
            override_pyobject_tainted=True,
        )

        request.form = taint_structure(
            request.form,
            OriginType.PARAMETER_NAME,
            OriginType.PARAMETER,
            override_pyobject_tainted=True,
        )


def _on_django_finalize_response_pre(ctx, after_request_tags, request, response):
    if (
        not response
        or not asm_config._iast_enabled
        or not asm_config.is_iast_request_enabled
        or get_iast_stacktrace_reported()
    ):
        return

    try:
        from .taint_sinks.stacktrace_leak import iast_check_stacktrace_leak

        content = response.content.decode("utf-8", errors="ignore")
        iast_check_stacktrace_leak(content)
    except Exception:
        iast_propagation_listener_log_log("Unexpected exception checking for stacktrace leak", exc_info=True)


def _on_django_technical_500_response(request, response, exc_type, exc_value, tb):
    if not exc_value or not asm_config._iast_enabled or not asm_config.is_iast_request_enabled:
        return

    try:
        from .taint_sinks.stacktrace_leak import asm_report_stacktrace_leak_from_django_debug_page

        exc_name = exc_type.__name__
        module = tb.tb_frame.f_globals.get("__name__", "")
        asm_report_stacktrace_leak_from_django_debug_page(exc_name, module)
    except Exception:
        iast_propagation_listener_log_log(
            "Unexpected exception checking for stacktrace leak on 500 response view", exc_info=True
        )


def _on_flask_finalize_request_post(response, _):
    if (
        not response
        or not asm_config._iast_enabled
        or not asm_config.is_iast_request_enabled
        or get_iast_stacktrace_reported()
    ):
        return

    try:
        from .taint_sinks.stacktrace_leak import iast_check_stacktrace_leak

        content = response[0].decode("utf-8", errors="ignore")

        iast_check_stacktrace_leak(content)
    except Exception:
        log.debug("Unexpected exception checking for stacktrace leak", exc_info=True)


def _on_asgi_finalize_response(body, _):
    if not body or not asm_config._iast_enabled or not asm_config.is_iast_request_enabled:
        return

    try:
        from .taint_sinks.stacktrace_leak import iast_check_stacktrace_leak

        content = body.decode("utf-8", errors="ignore")
        iast_check_stacktrace_leak(content)
    except Exception:
        log.debug("Unexpected exception checking for stacktrace leak", exc_info=True)


def _on_werkzeug_render_debugger_html(html):
    # we don't check asm_config.is_iast_request_enabled due to werkzeug.render_debugger_html works outside the request
    if not html or not asm_config._iast_enabled:
        return

    try:
        from .taint_sinks.stacktrace_leak import iast_check_stacktrace_leak

        iast_check_stacktrace_leak(html)
        set_iast_stacktrace_reported(True)
    except Exception:
        log.debug("Unexpected exception checking for stacktrace leak", exc_info=True)
