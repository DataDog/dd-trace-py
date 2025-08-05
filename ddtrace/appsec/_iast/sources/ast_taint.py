from typing import Any
from typing import Callable

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from ddtrace.settings.asm import config as asm_config

from ..constants import DEFAULT_SOURCE_IO_FUNCTIONS


def ast_function(
    func: Callable,
    flag_added_args: Any,
    *args: Any,
    **kwargs: Any,
) -> Any:
    instance = getattr(func, "__self__", None)
    func_name = getattr(func, "__name__", None)
    cls_name = ""
    if instance is not None and func_name:
        try:
            cls_name = instance.__class__.__name__
        except AttributeError:
            pass

    if flag_added_args > 0:
        args = args[flag_added_args:]

    module_name = instance.__class__.__module__
    result = func(*args, **kwargs)
    if (
        module_name == "_io"
        and cls_name in ("BytesIO", "StringIO")
        and func_name in DEFAULT_SOURCE_IO_FUNCTIONS[module_name]
    ):
        if asm_config._iast_enabled and asm_config.is_iast_request_enabled:
            ranges = get_tainted_ranges(instance)
            if len(ranges) > 0:
                source = (
                    ranges[0].source if ranges[0].source else Source(name="_io", value=result, origin=OriginType.EMPTY)
                )
                result = taint_pyobject(
                    pyobject=result,
                    source_name=source.name,
                    source_value=source.value,
                    source_origin=source.origin,
                )
    return result
