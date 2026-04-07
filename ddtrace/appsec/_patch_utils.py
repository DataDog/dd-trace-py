import ctypes
import os
import sysconfig
from typing import Any
from typing import Callable
from typing import Optional

from wrapt import FunctionWrapper
from wrapt import resolve_path

from ddtrace.internal._unpatched import _gc as gc
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog


log = get_logger(__name__)

# Lazy-cached reference to avoid loading _stacktrace (a C extension used only
# by IAST) when _patch_utils is imported by non-IAST code paths.
_get_info_frame = None

# Cached paths for relativizing file paths (computed once at import time).
_CWD = os.path.abspath(os.getcwd())
_PURELIB_PATH = sysconfig.get_path("purelib") or ""
_STDLIB_PATH = sysconfig.get_path("stdlib") or ""


def rel_path(file_name: str) -> str:
    """Relativize an absolute file path for vulnerability/reachability reporting.

    Used by both IAST and SCA to produce short, readable paths in telemetry
    payloads.  Tries purelib first, then stdlib, then CWD-relative, then
    site-packages.  Returns empty string if the path cannot be relativized.
    """
    file_name_norm = file_name.replace("\\", "/")
    if file_name_norm.startswith(_PURELIB_PATH):
        return os.path.relpath(file_name_norm, start=_PURELIB_PATH)

    if file_name_norm.startswith(_STDLIB_PATH):
        return os.path.relpath(file_name_norm, start=_STDLIB_PATH)
    if file_name_norm.startswith(_CWD):
        return os.path.relpath(file_name_norm, start=_CWD)
    # If the path contains site-packages anywhere, return 'site-packages/<rest>'
    # Normalize separators to forward slashes for consistency
    if (idx := file_name_norm.find("/site-packages/")) != -1:
        return file_name_norm[idx:]
    return ""


def get_caller_frame_info() -> tuple:
    """Walk the stack and return (file_name, line_number, function_name, class_name).

    Uses the native C get_info_frame() to skip ddtrace, stdlib, and special
    frames, then relativizes the path.  Shared by IAST vulnerability
    reporting and SCA reachability detection.

    Returns (None, None, None, None) when no relevant frame is found.
    """
    global _get_info_frame
    if _get_info_frame is None:
        from ddtrace.appsec._shared._stacktrace import get_info_frame

        _get_info_frame = get_info_frame
    frame_info = _get_info_frame()
    if not frame_info or frame_info[0] in ("", -1, None):
        return None, None, None, None

    file_name, line_number, function_name, class_name = frame_info
    if not file_name:
        return None, None, None, None

    file_name = rel_path(file_name)
    if not file_name:
        return None, None, None, None

    return file_name, line_number, function_name, class_name


_DD_ORIGINAL_ATTRIBUTES: dict[Any, Any] = {}


def try_unwrap(module: Any, name: str) -> None:
    try:
        (parent, attribute, _) = resolve_path(module, name)
        if (parent, attribute) in _DD_ORIGINAL_ATTRIBUTES:
            original = _DD_ORIGINAL_ATTRIBUTES[(parent, attribute)]
            apply_patch(parent, attribute, original)
            del _DD_ORIGINAL_ATTRIBUTES[(parent, attribute)]
    except (ModuleNotFoundError, AttributeError):
        log.debug("ERROR unwrapping %s.%s ", module, name)


def try_wrap_function_wrapper(module_name: str, name: str, wrapper: Callable[..., Any]) -> None:
    @ModuleWatchdog.after_module_imported(module_name)
    def _(module: Any) -> None:
        try:
            wrap_object(module, name, FunctionWrapper, (wrapper,))
        except (ImportError, AttributeError):
            log.debug("Module %s.%s does not exist", module_name, name)


def wrap_object(
    module: Any,
    name: str,
    factory: Callable[..., Any],
    args: tuple[Any, ...] = (),
    kwargs: Optional[dict[str, Any]] = None,
) -> Any:
    if kwargs is None:
        kwargs = {}
    (parent, attribute, original) = resolve_path(module, name)
    wrapper = factory(original, *args, **kwargs)
    apply_patch(parent, attribute, wrapper)
    wrapper.__deepcopy__ = lambda memo: wrapper
    return wrapper


def apply_patch(parent: Any, attribute: str, replacement: Any) -> None:
    try:
        current_attribute = getattr(parent, attribute)
        # Avoid overwriting the original function if we call this twice
        if not isinstance(current_attribute, FunctionWrapper):
            _DD_ORIGINAL_ATTRIBUTES[(parent, attribute)] = current_attribute
        elif isinstance(replacement, FunctionWrapper) and (
            getattr(replacement, "_self_wrapper", None) is getattr(current_attribute, "_self_wrapper", None)
        ):
            # Avoid double patching
            return
        setattr(parent, attribute, replacement)
    except (TypeError, AttributeError):
        patch_builtins(parent, attribute, replacement)


def patchable_builtin(klass: type[Any]) -> Any:
    refs = gc.get_referents(klass.__dict__)
    return refs[0]


def patch_builtins(klass: type[Any], attr: str, value: Any) -> None:
    """Based on forbiddenfruit package:
    https://github.com/clarete/forbiddenfruit/blob/master/forbiddenfruit/__init__.py#L421
    ---
    Patch a built-in `klass` with `attr` set to `value`

    This function monkey-patches the built-in python object `attr` adding a new
    attribute to it. You can add any kind of argument to the `class`.

    It's possible to attach methods as class methods, just do the following:

      >>> def myclassmethod(cls):
      ...     return cls(1.5)
      >>> curse(float, "myclassmethod", classmethod(myclassmethod))
      >>> float.myclassmethod()
      1.5

    Methods will be automatically bound, so don't forget to add a self
    parameter to them, like this:

      >>> def hello(self):
      ...     return self * 2
      >>> curse(str, "hello", hello)
      >>> "yo".hello()
      "yoyo"
    """
    dikt = patchable_builtin(klass)

    old_value = dikt.get(attr, None)
    old_name = "_c_%s" % attr  # do not use .format here, it breaks py2.{5,6}

    # Patch the thing
    dikt[attr] = value

    if old_value:
        dikt[old_name] = old_value

        try:
            dikt[attr].__name__ = old_value.__name__
        except (AttributeError, TypeError):  # py2.5 will raise `TypeError`
            pass
        try:
            dikt[attr].__qualname__ = old_value.__qualname__
        except AttributeError:
            pass

    ctypes.pythonapi.PyType_Modified(ctypes.py_object(klass))
