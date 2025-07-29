from typing import Text

from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config

from ..._constants import IAST
from .._patch_modules import WrapFunctonsForIAST


log = get_logger(__name__)


def get_version() -> Text:
    return ""


_IS_PATCHED = False


def patch():
    global _IS_PATCHED
    if _IS_PATCHED and not asm_config._iast_is_testing:
        return

    if not asm_config._iast_enabled:
        return

    iast_funcs = WrapFunctonsForIAST()
    iast_funcs.wrap_function("json", "loads", wrapped_loads)
    if asm_config._iast_lazy_taint:
        iast_funcs.wrap_function("json.encoder", "JSONEncoder.default", patched_json_encoder_default)
        iast_funcs.wrap_function("simplejson.encoder", "JSONEncoder.default", patched_json_encoder_default)

    iast_funcs.patch()


def wrapped_loads(wrapped, instance, args, kwargs):
    obj = wrapped(*args, **kwargs)
    if asm_config._iast_enabled and asm_config.is_iast_request_enabled:
        try:
            from .._taint_tracking._taint_objects import taint_pyobject
            from .._taint_tracking._taint_objects_base import get_tainted_ranges
            from .._taint_utils import taint_structure

            ranges = get_tainted_ranges(args[0])

            if ranges and obj:
                # take the first source as main source
                source = ranges[0].source
                if isinstance(obj, dict):
                    obj = taint_structure(obj, source.origin, source.origin)
                elif isinstance(obj, list):
                    obj = taint_structure(obj, source.origin, source.origin)
                elif isinstance(obj, IAST.TEXT_TYPES):
                    obj = taint_pyobject(obj, source.name, source.value, source.origin)
        except Exception:
            log.debug("Unexpected exception while reporting vulnerability", exc_info=True)
    return obj


def patched_json_encoder_default(original_func, instance, args, kwargs):
    from .._taint_utils import LazyTaintDict
    from .._taint_utils import LazyTaintList

    if isinstance(args[0], (LazyTaintList, LazyTaintDict)):
        return args[0]._obj

    return original_func(*args, **kwargs)
