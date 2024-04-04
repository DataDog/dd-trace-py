import ctypes
import gc
from typing import Any
from typing import Callable
from typing import Dict

from ddtrace.internal.logger import get_logger
from ddtrace.vendor.wrapt import FunctionWrapper
from ddtrace.vendor.wrapt import resolve_path
from ddtrace.vendor.wrapt.importer import when_imported


log = get_logger(__name__)
_DD_ORIGINAL_ATTRIBUTES: Dict[Any, Any] = {}

COMMON_PATCH_MODULES = {
    "urllib": ("ssrf",),
    "builtins": ("lfi_path_traversal",),
}


def patch_common_modules(patch_modules=COMMON_PATCH_MODULES):
    from ddtrace._monkey import _on_import_factory
    from ddtrace.appsec._common.taint_sinks.lfi_path_traversal import wrapped_open_CFDDB7ABBA9081B6

    for python_module, vuln_modules in patch_modules.items():
        for vuln_module in vuln_modules:
            # Skip builtins as it has to be patched regardless of imports
            if python_module == "builtins":
                continue

            when_imported(python_module)(
                _on_import_factory(vuln_module, prefix="ddtrace.appsec._common.taint_sinks", raise_errors=False)
            )

    # Patch builtins module
    try_wrap_function_wrapper("builtins", "open", wrapped_open_CFDDB7ABBA9081B6)


def unpatch_common_modules():
    try_unwrap("urllib.request", "OpenerDirector.open")
    try_unwrap("builtins", "open")


def try_unwrap(module, name):
    try:
        (parent, attribute, _) = resolve_path(module, name)
        if (parent, attribute) in _DD_ORIGINAL_ATTRIBUTES:
            original = _DD_ORIGINAL_ATTRIBUTES[(parent, attribute)]
            apply_patch(parent, attribute, original)
            del _DD_ORIGINAL_ATTRIBUTES[(parent, attribute)]
    except ModuleNotFoundError:
        pass


def try_wrap_function_wrapper(module: str, name: str, wrapper: Callable) -> None:
    try:
        wrap_object(module, name, FunctionWrapper, (wrapper,))
    except (ImportError, AttributeError):
        log.debug("ASM patching. Module %s.%s does not exist", module, name)


def wrap_object(module, name, factory, args=(), kwargs=None):
    if kwargs is None:
        kwargs = {}
    (parent, attribute, original) = resolve_path(module, name)
    wrapper = factory(original, *args, **kwargs)
    apply_patch(parent, attribute, wrapper)
    return wrapper


def apply_patch(parent, attribute, replacement):
    try:
        current_attribute = getattr(parent, attribute)
        # Avoid overwriting the original function if we call this twice
        if not isinstance(current_attribute, FunctionWrapper):
            _DD_ORIGINAL_ATTRIBUTES[(parent, attribute)] = current_attribute
        setattr(parent, attribute, replacement)
    except (TypeError, AttributeError):
        patch_builtins(parent, attribute, replacement)


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
