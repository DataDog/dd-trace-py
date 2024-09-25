import functools
import sys
from typing import Callable
from typing import Text

from wrapt import FunctionWrapper

from ddtrace.appsec._common_module_patches import wrap_object
from ddtrace.internal.logger import get_logger

from ._metrics import _set_metric_iast_instrumented_source
from ._taint_utils import taint_structure
from ._utils import _is_iast_enabled


log = get_logger(__name__)


def set_and_check_module_is_patched(module_str: Text, default_attr: Text = "_datadog_patch") -> bool:
    try:
        __import__(module_str)
        module = sys.modules[module_str]
        if getattr(module, default_attr, False):
            return False
        setattr(module, default_attr, True)
    except ImportError:
        pass
    return True


def set_module_unpatched(module_str: Text, default_attr: Text = "_datadog_patch"):
    try:
        __import__(module_str)
        module = sys.modules[module_str]
        setattr(module, default_attr, False)
    except ImportError:
        pass


def try_wrap_function_wrapper(module: Text, name: Text, wrapper: Callable):
    try:
        wrap_object(module, name, FunctionWrapper, (wrapper,))
    except (ImportError, AttributeError):
        log.debug("IAST patching. Module %s.%s not exists", module, name)


def if_iast_taint_returned_object_for(origin, wrapped, instance, args, kwargs):
    value = wrapped(*args, **kwargs)

    if _is_iast_enabled():
        try:
            from ._taint_tracking import is_pyobject_tainted
            from ._taint_tracking import taint_pyobject
            from .processor import AppSecIastSpanProcessor

            if not AppSecIastSpanProcessor.is_span_analyzed():
                return value

            if not is_pyobject_tainted(value):
                name = str(args[0]) if len(args) else "http.request.body"
                from ddtrace.appsec._iast._taint_tracking import OriginType

                if origin == OriginType.HEADER and name.lower() in ["cookie", "cookies"]:
                    origin = OriginType.COOKIE
                return taint_pyobject(pyobject=value, source_name=name, source_value=value, source_origin=origin)
        except Exception:
            log.debug("Unexpected exception while tainting pyobject", exc_info=True)
    return value


def if_iast_taint_yield_tuple_for(origins, wrapped, instance, args, kwargs):
    if _is_iast_enabled():
        from ._taint_tracking import taint_pyobject
        from .processor import AppSecIastSpanProcessor

        if not AppSecIastSpanProcessor.is_span_analyzed():
            for key, value in wrapped(*args, **kwargs):
                yield key, value
        else:
            for key, value in wrapped(*args, **kwargs):
                new_key = taint_pyobject(pyobject=key, source_name=key, source_value=key, source_origin=origins[0])
                new_value = taint_pyobject(
                    pyobject=value, source_name=key, source_value=value, source_origin=origins[1]
                )
                yield new_key, new_value

    else:
        for key, value in wrapped(*args, **kwargs):
            yield key, value


def _patched_dictionary(origin_key, origin_value, original_func, instance, args, kwargs):
    result = original_func(*args, **kwargs)

    return taint_structure(result, origin_key, origin_value, override_pyobject_tainted=True)


def _patched_fastapi_function(origin, original_func, instance, args, kwargs):
    result = original_func(*args, **kwargs)

    if _is_iast_enabled():
        try:
            from ._taint_tracking import is_pyobject_tainted
            from .processor import AppSecIastSpanProcessor

            if not AppSecIastSpanProcessor.is_span_analyzed():
                return result

            if not is_pyobject_tainted(result):
                from ._taint_tracking import origin_to_str
                from ._taint_tracking import taint_pyobject

                return taint_pyobject(
                    pyobject=result, source_name=origin_to_str(origin), source_value=result, source_origin=origin
                )
        except Exception:
            log.debug("Unexpected exception while tainting pyobject", exc_info=True)
    return result


def _on_iast_fastapi_patch():
    from ddtrace.appsec._iast._taint_tracking import OriginType

    # Cookies sources
    try_wrap_function_wrapper(
        "starlette.requests",
        "cookie_parser",
        functools.partial(_patched_dictionary, OriginType.COOKIE_NAME, OriginType.COOKIE),
    )
    try_wrap_function_wrapper(
        "fastapi",
        "Cookie",
        functools.partial(_patched_fastapi_function, OriginType.COOKIE_NAME),
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
    try_wrap_function_wrapper(
        "fastapi",
        "Header",
        functools.partial(_patched_fastapi_function, OriginType.HEADER),
    )
    _set_metric_iast_instrumented_source(OriginType.HEADER)

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
    _set_metric_iast_instrumented_source(OriginType.BODY)

    # Instrumented on _iast_starlette_scope_taint
    _set_metric_iast_instrumented_source(OriginType.PATH_PARAMETER)


def _iast_instrument_starlette_url(wrapped, instance, args, kwargs):
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import origin_to_str
    from ddtrace.appsec._iast._taint_tracking import taint_pyobject

    def path(self) -> str:
        return taint_pyobject(
            self.components.path,
            source_name=origin_to_str(OriginType.PATH),
            source_value=self.components.path,
            source_origin=OriginType.PATH,
        )

    instance.__class__.path = property(path)
    wrapped(*args, **kwargs)


def _iast_instrument_starlette_request(wrapped, instance, args, kwargs):
    from ddtrace.appsec._iast._taint_tracking import OriginType

    def receive(self):
        """This pattern comes from a Request._receive property, which returns a callable"""

        async def wrapped_property_call():
            body = await self._receive()
            return taint_structure(body, OriginType.BODY, OriginType.BODY, override_pyobject_tainted=True)

        return wrapped_property_call

    # `self._receive` is set in `__init__`, so we wait for the constructor to finish before setting the new property
    wrapped(*args, **kwargs)
    instance.__class__.receive = property(receive)


async def _iast_instrument_starlette_request_body(wrapped, instance, args, kwargs):
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import origin_to_str
    from ddtrace.appsec._iast._taint_tracking import taint_pyobject

    result = await wrapped(*args, **kwargs)

    return taint_pyobject(
        result, source_name=origin_to_str(OriginType.PATH), source_value=result, source_origin=OriginType.BODY
    )


def _iast_instrument_starlette_scope(scope):
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import taint_pyobject

    if scope.get("path_params"):
        try:
            for k, v in scope["path_params"].items():
                scope["path_params"][k] = taint_pyobject(
                    v, source_name=k, source_value=v, source_origin=OriginType.PATH_PARAMETER
                )
        except Exception:
            log.debug("IAST: Unexpected exception while tainting path parameters", exc_info=True)
