from collections import defaultdict
import ctypes
import gc

from ddtrace.internal.logger import get_logger
from ddtrace.vendor.wrapt import FunctionWrapper
from ddtrace.vendor.wrapt import resolve_path


_DD_ORIGINAL_ATTRIBUTES = {}  # types: Dict[Any, Any]

log = get_logger(__name__)
__hidden_elements__ = defaultdict(list)


def _wrap_function_wrapper_exception(module, name, wrapper):
    try:
        wrap_function_wrapper(module, name, wrapper)
    except (ImportError, AttributeError):
        log.debug("IAST patching. Module %s.%s not exists", module, name)


def _unwrap_exception(module, name):
    (parent, attribute, _) = resolve_path(module, name)
    if (parent, attribute) in _DD_ORIGINAL_ATTRIBUTES:
        original = _DD_ORIGINAL_ATTRIBUTES[(parent, attribute)]
        apply_patch(parent, attribute, original)
        del _DD_ORIGINAL_ATTRIBUTES[(parent, attribute)]


def wrap_function_wrapper(module, name, wrapper):
    return wrap_object(module, name, FunctionWrapper, (wrapper,))


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


def patch_builtins(klass, attr, value, hide_from_dir=False):
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
        hide_from_dir = False  # It was already in dir
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

    if hide_from_dir:
        __hidden_elements__[klass.__name__].append(attr)


def unpatch_builtins(klass, attr):
    """Reverse a curse in a built-in object
    This function removes *new* attributes. It's actually possible to remove
    any kind of attribute from any built-in class, but just DON'T DO IT :)
    Good:
      >>> curse(str, "blah", "bleh")
      >>> assert "blah" in dir(str)
      >>> reverse(str, "blah")
      >>> assert "blah" not in dir(str)

    Bad:
      >>> reverse(str, "strip")
      >>> " blah ".strip()
      Traceback (most recent call last):
        File "<stdin>", line 1, in <module>
      AttributeError: 'str' object has no attribute 'strip'
    """
    dikt = patchable_builtin(klass)
    del dikt[attr]

    ctypes.pythonapi.PyType_Modified(ctypes.py_object(klass))
