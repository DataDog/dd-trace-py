import sys
from typing import Callable
from typing import Text

from wrapt import FunctionWrapper

from ddtrace.appsec._common_module_patches import wrap_object
from ddtrace.appsec._iast._logs import iast_instrumentation_wrapt_debug_log
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import origin_to_str
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject


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
        iast_instrumentation_wrapt_debug_log(f"Module {module}.{name} not exists")


def _iast_instrument_starlette_url(wrapped, instance, args, kwargs):
    def path(self) -> str:
        return taint_pyobject(
            self.components.path,
            source_name=origin_to_str(OriginType.PATH),
            source_value=self.components.path,
            source_origin=OriginType.PATH,
        )

    instance.__class__.path = property(path)
    wrapped(*args, **kwargs)
