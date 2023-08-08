import ctypes
import gc
import sys
from typing import TYPE_CHECKING

from ddtrace.appsec.iast._util import _is_iast_enabled
from ddtrace.internal.logger import get_logger
from ddtrace.vendor.wrapt import FunctionWrapper
from ddtrace.vendor.wrapt import resolve_path


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Callable
    from typing import Dict
    from typing import Optional


_DD_ORIGINAL_ATTRIBUTES = {}  # type: Dict[Any, Any]

log = get_logger(__name__)


def set_and_check_module_is_patched(module_str, default_attr="_datadog_patch"):
    # type: (str, str) -> Optional[bool]
    try:
        __import__(module_str)
        module = sys.modules[module_str]
        if getattr(module, default_attr, False):
            return False
        setattr(module, default_attr, True)
    except ImportError:
        pass
    return True


def set_module_unpatched(module_str, default_attr="_datadog_patch"):
    # type: (str, str) -> None
    try:
        __import__(module_str)
        module = sys.modules[module_str]
        setattr(module, default_attr, False)
    except ImportError:
        pass


def try_wrap_function_wrapper(module, name, wrapper):
    # type: (str, str, Callable) -> None
    try:
        wrap_object(module, name, FunctionWrapper, (wrapper,))
    except (ImportError, AttributeError):
        log.debug("IAST patching. Module %s.%s not exists", module, name)


def try_unwrap(module, name):
    try:
        (parent, attribute, _) = resolve_path(module, name)
        if (parent, attribute) in _DD_ORIGINAL_ATTRIBUTES:
            original = _DD_ORIGINAL_ATTRIBUTES[(parent, attribute)]
            apply_patch(parent, attribute, original)
            del _DD_ORIGINAL_ATTRIBUTES[(parent, attribute)]
    except ModuleNotFoundError:
        pass


def apply_patch(parent, attribute, replacement):
    try:
        current_attribute = getattr(parent, attribute)
        # Avoid overwriting the original function if we call this twice
        if not isinstance(current_attribute, FunctionWrapper):
            _DD_ORIGINAL_ATTRIBUTES[(parent, attribute)] = current_attribute
        setattr(parent, attribute, replacement)
    except (TypeError, AttributeError):
        patch_builtins(parent, attribute, replacement)


def wrap_object(module, name, factory, args=(), kwargs={}):
    (parent, attribute, original) = resolve_path(module, name)
    wrapper = factory(original, *args, **kwargs)
    apply_patch(parent, attribute, wrapper)
    return wrapper


def patchable_builtin(klass):
    refs = gc.get_referents(klass.__dict__)
    return refs[0]


def patch_builtins(klass, attr, value):
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


def if_iast_taint_returned_object_for(origin, wrapped, instance, args, kwargs):
    value = wrapped(*args, **kwargs)

    if _is_iast_enabled():
        try:
            from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted
            from ddtrace.appsec.iast._taint_tracking import taint_pyobject

            if not is_pyobject_tainted(value):
                name = str(args[0]) if len(args) else "http.request.body"
                return taint_pyobject(pyobject=value, source_name=name, source_value=value, source_origin=origin)
        except Exception:
            log.debug("Unexpected exception while tainting pyobject", exc_info=True)
    return value


def if_iast_taint_yield_tuple_for(origins, wrapped, instance, args, kwargs):
    if _is_iast_enabled():
        from ddtrace.appsec.iast._taint_tracking import taint_pyobject

        for key, value in wrapped(*args, **kwargs):
            new_key = taint_pyobject(pyobject=key, source_name=key, source_value=key, source_origin=origins[0])
            new_value = taint_pyobject(pyobject=value, source_name=key, source_value=value, source_origin=origins[1])
            yield new_key, new_value

    else:
        for key, value in wrapped(*args, **kwargs):
            yield key, value
