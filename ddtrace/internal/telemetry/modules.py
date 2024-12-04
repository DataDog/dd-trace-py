import ctypes
import gc
import inspect
import sys
from typing import Any
from typing import Callable
from typing import Dict
from typing import Set
from typing import Tuple

from wrapt import FunctionWrapper
from wrapt import resolve_path

from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog


log = get_logger(__name__)

NEW_MODULES: Set[Tuple[str, str]] = set()  # New modules that have been imported since the last check
ALL_MODULES: Set[Tuple[str, str]] = set()  # All modules that have been imported
MODULE_HOOK_INSTALLED = False
GLOBAL_CALLS = 0


def sbom_collection(original_import_callable, instance, args, kwargs):
    """
    wrapper for import function
    """
    global GLOBAL_CALLS
    GLOBAL_CALLS += 1
    imported_module = args[0]
    if not imported_module.startswith("ddtrace"):
        # Avoid reporting importlib as parent module
        importing_module = "importlib"
        frame = inspect.currentframe().f_back
        while importing_module.startswith("importlib"):
            if frame is None:
                return original_import_callable(*args, **kwargs)
            importing_module = frame.f_globals.get("__name__", "__unknown__")
            frame = frame.f_back

        edge = (imported_module, importing_module)
        global NEW_MODULES, ALL_MODULES
        if edge not in ALL_MODULES:
            NEW_MODULES.add(edge)
            ALL_MODULES.add(edge)

    return original_import_callable(*args, **kwargs)


def get_newly_imported_modules() -> Set[Tuple[str, str]]:
    global MODULE_HOOK_INSTALLED, NEW_MODULES, ALL_MODULES, GLOBAL_CALLS

    info = f"||| MODULE_HOOK_INSTALLED: {MODULE_HOOK_INSTALLED} {GLOBAL_CALLS}"
    log.error(info)
    # Our hook is not installed, so we are not getting notified of new imports,
    # we need to track the changes manually
    if not NEW_MODULES and not MODULE_HOOK_INSTALLED:
        latest_modules = {(module, "__unknown__") for module in sys.modules}
        NEW_MODULES = latest_modules - ALL_MODULES
        ALL_MODULES = latest_modules

    new_modules = NEW_MODULES
    NEW_MODULES = set()
    return new_modules


def install_import_hook():
    global MODULE_HOOK_INSTALLED, NEW_MODULES, ALL_MODULES

    # If we have not called get_newly_imported_modules yet, we can initialize to all imported modules
    if not NEW_MODULES:
        NEW_MODULES = {(module, "ddtrace") for module in sys.modules}
        ALL_MODULES = NEW_MODULES.copy()
    try_wrap_function_wrapper("builtins", "__import__", sbom_collection)
    MODULE_HOOK_INSTALLED = True


def uninstall_import_hook():
    # We cannot uninstall a sys audit hook
    pass


def try_wrap_function_wrapper(module_name: str, name: str, wrapper: Callable) -> None:
    @ModuleWatchdog.after_module_imported(module_name)
    def _(module):
        try:
            wrap_object(module, name, FunctionWrapper, (wrapper,))
        except (ImportError, AttributeError):
            log.debug("ASM patching. Module %s.%s does not exist", module_name, name)


def wrap_object(module, name, factory, args=(), kwargs=None):
    if kwargs is None:
        kwargs = {}
    (parent, attribute, original) = resolve_path(module, name)
    wrapper = factory(original, *args, **kwargs)
    apply_patch(parent, attribute, wrapper)
    return wrapper


_DD_ORIGINAL_ATTRIBUTES: Dict[Any, Any] = {}


def apply_patch(parent, attribute, replacement):
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
