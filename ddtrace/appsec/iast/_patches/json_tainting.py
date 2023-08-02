from ddtrace import config
from ddtrace.appsec.iast._patch import set_and_check_module_is_patched
from ddtrace.appsec.iast._patch import set_module_unpatched
from ddtrace.appsec.iast._patch import try_unwrap
from ddtrace.appsec.iast._patch import try_wrap_function_wrapper
from ddtrace.appsec.iast._taint_utils import LazyTaintDict
from ddtrace.appsec.iast._taint_utils import LazyTaintList
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


_DEFAULT_ATTR = "_datadog_json_tainting_patch"


def unpatch_iast():
    # type: () -> None
    set_module_unpatched("json", default_attr=_DEFAULT_ATTR)
    try_unwrap("json", "loads")


def patch():
    # type: () -> None
    """Wrap functions which interact with file system."""
    if not set_and_check_module_is_patched("json", default_attr=_DEFAULT_ATTR):
        return
    try_wrap_function_wrapper("json", "loads", wrapped_loads)


def wrapped_loads(wrapped, instance, args, kwargs):
    obj = wrapped(*args, **kwargs)
    if config._iast_enabled:
        try:
            from ddtrace.appsec.iast._taint_tracking import get_tainted_ranges
            from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted
            from ddtrace.appsec.iast._taint_tracking import taint_pyobject

            if is_pyobject_tainted(args[0]) and obj:
                # tainting object
                ranges = get_tainted_ranges(args[0])
                if not ranges:
                    return obj
                # take the first source as main source
                source = ranges[0].source
                if isinstance(obj, dict):
                    obj = LazyTaintDict(obj, origins=(source.origin, source.origin))
                elif isinstance(obj, list):
                    obj = LazyTaintList(obj, origins=(source.origin, source.origin))
                elif isinstance(obj, (str, bytes, bytearray)):
                    obj = taint_pyobject(obj, source.name, source.value, source.origin)
                pass
        except Exception:
            log.debug("Unexpected exception while reporting vulnerability", exc_info=True)
            raise
    return obj
