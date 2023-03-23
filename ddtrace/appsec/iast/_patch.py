import ctypes
import gc
import sys
from typing import TYPE_CHECKING

from ddtrace.appsec.iast._input_info import Input_info
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
    (parent, attribute, _) = resolve_path(module, name)
    if (parent, attribute) in _DD_ORIGINAL_ATTRIBUTES:
        original = _DD_ORIGINAL_ATTRIBUTES[(parent, attribute)]
        apply_patch(parent, attribute, original)
        del _DD_ORIGINAL_ATTRIBUTES[(parent, attribute)]


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
    assert len(refs) == 1
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
    if _is_iast_enabled():
        from ddtrace.appsec.iast._taint_tracking import taint_pyobject

        value = wrapped(*args, **kwargs)

        name = str(args[0]) if len(args) else "http.request.body"
        return taint_pyobject(value, Input_info(name, value, origin))

    return wrapped(*args, **kwargs)
