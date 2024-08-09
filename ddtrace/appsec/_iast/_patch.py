import sys
from typing import Callable
from typing import Text

from wrapt import FunctionWrapper

from ddtrace.appsec._common_module_patches import wrap_object
from ddtrace.internal.logger import get_logger

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
