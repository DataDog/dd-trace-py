from typing import Text

from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.asm import config as asm_config

from .._patch_modules import WrapFunctonsForIAST


log = get_logger(__name__)


def get_version() -> Text:
    return ""


_IS_PATCHED = False


def patch():
    """Patch JSON encoder for lazy taint support.

    Note: json.loads taint propagation is now handled via AST rewriting in visitor.py
    (module_functions) instead of global wrapping, to avoid tainting internal/library
    calls (e.g. RemoteConfig polling) which caused memory leaks.
    """
    global _IS_PATCHED
    if _IS_PATCHED and not asm_config._iast_is_testing:
        return

    if not asm_config._iast_enabled:
        return

    if asm_config._iast_lazy_taint:
        iast_funcs = WrapFunctonsForIAST()
        iast_funcs.wrap_function("json.encoder", "JSONEncoder.default", patched_json_encoder_default)
        iast_funcs.wrap_function("simplejson.encoder", "JSONEncoder.default", patched_json_encoder_default)
        iast_funcs.patch()

    _IS_PATCHED = True


def patched_json_encoder_default(original_func, instance, args, kwargs):
    from .._taint_utils import LazyTaintDict
    from .._taint_utils import LazyTaintList

    if isinstance(args[0], (LazyTaintList, LazyTaintDict)):
        return args[0]._obj

    return original_func(*args, **kwargs)
