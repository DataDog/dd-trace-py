from ddtrace import config
from ddtrace.internal.logger import get_logger

from .._patch import set_and_check_module_is_patched
from .._patch import set_module_unpatched
from .._patch import try_unwrap
from .._patch import try_wrap_function_wrapper
from .._taint_utils import LazyTaintDict
from .._taint_utils import LazyTaintList


log = get_logger(__name__)


_DEFAULT_ATTR = "_datadog_json_tainting_patch"


def get_version():
    # type: () -> str
    return ""


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
            from .._taint_tracking import get_tainted_ranges
            from .._taint_tracking import is_pyobject_tainted
            from .._taint_tracking import taint_pyobject

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
