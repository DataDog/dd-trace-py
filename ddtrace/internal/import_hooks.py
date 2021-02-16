"""
Module for patching Python's internal import system for import hooks.

There are a few ways to monitor Python imports, but we have found the following method
of patching internal Python functions and builtin functions the most reliable and
least invasive process.

Given the challenges faced by implementing PEP 302, and our experience with monkey patching
and wrapping internal Python functions was an easier and more reliable way
of implementing this feature in a way that has little effect on other packages.

For us to successfully use import hooks, we need to ensure that the hooks are always called
regardless of the condition or means of importing the module.


PEP 302 defines a process for adding import "hooks".
https://www.python.org/dev/peps/pep-0302/

The way it works is by adding a custom "finder" onto `sys.meta_path`,
for example: `sys.meta_path.append(MyFinder)`

Finders are a way for users to customize the way the Python import system
finds and loads modules.

This approach has a few flaws

1) Finders are called in order. In order for us to always ensure we are able to capture
every import we would need to make sure that our Finder is always first in `sys.meta_path`.

This can become tricky when the user uses another package that adds to `sys.meta_path`
(e.g. `six`, or `newrelic`).

If another Finder runs before ours and finds the module they are looking for, then ours will
never get called.

2) Finders were never meant to be used this way. An example of a good finder is a custom
finder that looks for and loads a module off of a shared central server instead of a
location on the existing Python path, or one that knows how to load modules from a `.zip`
or another file format.

3) The code is a little complex. In order to write a no-op finder we need to ensure
other finders are called by us, and then at the end we look if they found a module and return it.

This is the approach `wrapt` went for and requires locking and keeping the state of the current module
being loaded and re-calling `__import__` for the module to trigger the other finders.

4) Reloading a module is a weird case that doesn't always trigger a module finder.


For these reasons we have decided to patch Python's internal module loading functions instead.
"""
import sys
import threading

from ..compat import PY3
from ..vendor import wrapt
from .logger import get_logger


__all__ = ["hooks", "register_module_hook", "patch", "unpatch"]

log = get_logger(__name__)

ORIGINAL_IMPORT = __import__


class ModuleHookRegistry(object):
    """
    Registry to keep track of all module import hooks defined
    """

    __slots__ = ("hooks", "lock")

    def __init__(self):
        """
        Initialize a new registry
        """
        self.lock = threading.Lock()
        self.reset()

    def register(self, name, func):
        """
        Register a new hook function for the provided module name

        If the module is already loaded, then ``func`` is called immediately

        :param name: The name of the module to add the hook for (e.g. 'requests', 'flask.app', etc)
        :type name: str
        :param func: The function to register as a hook
        :type func: function(module)
        """
        with self.lock:
            if name not in self.hooks:
                self.hooks[name] = set([func])
            else:
                self.hooks[name].add(func)

            # Module is already loaded, call hook right away
            if name in sys.modules:
                func(sys.modules[name])

    def deregister(self, name, func):
        """
        Deregister an already registered hook function

        :param name: The name of the module the hook was for (e.g. 'requests', 'flask.app', etc)
        :type name: str
        :param func: The function that was registered previously
        :type func: function(module)
        """
        with self.lock:
            # If no hooks exist for this module, return
            if name not in self.hooks:
                log.debug("No hooks registered for module %r", name)
                return

            # Remove this function from the hooks if exists
            if func in self.hooks[name]:
                self.hooks[name].remove(func)
            else:
                log.debug("No hook %r registered for module %r", func, name)

    def call(self, name, module=None):
        """
        Call all hooks for the provided module

        If no module was provided then we will attempt to grab from ``sys.modules`` first

        :param name: The name of the module to call hooks for (e.g. 'requests', 'flask.app', etc)
        :type name: str
        :param module: Optional, the module object to pass to hook functions
        :type module: Module|None
        """
        with self.lock:
            # Make sure we have hooks for this module
            if not self.hooks.get(name):
                log.debug("No hooks registered for module %r", name)
                return

            # Try to fetch from `sys.modules` if one wasn't given directly
            if module is None:
                module = sys.modules.get(name)

            # No module found, don't call anything
            if not module:
                log.warning("Tried to call hooks for unloaded module %r", name)
                return

            # Call all hooks for this module
            for hook in self.hooks[name]:
                try:
                    hook(module)
                except Exception:
                    log.warning("Failed to call hook %r for module %r", hook, name, exc_info=True)

    def reset(self):
        """Reset/remove all registered hooks"""
        with self.lock:
            self.hooks = dict()


# Default/global module hook registry
hooks = ModuleHookRegistry()


def exec_and_call_hooks(module_name, wrapped, args, kwargs):
    """
    Helper used to execute the wrapped function with args/kwargs and then call any
      module hooks for `module_name` after
    """
    try:
        return wrapped(*args, **kwargs)
    finally:
        # Never let this function fail to execute
        try:
            # DEV: `hooks.call()` will only call hooks if the module was successfully loaded
            hooks.call(module_name)
        except Exception:
            log.debug("Failed to call hooks for module %r", module_name, exc_info=True)


def wrapped_reload(wrapped, instance, args, kwargs):
    """
    Wrapper for `importlib.reload` to we can trigger hooks on a module reload
    """
    module_name = None
    try:
        # Python 3 added specs, no need to even check for `__spec__` if we are in Python 2
        if PY3:
            try:
                module_name = args[0].__spec__.name
            except AttributeError:
                module_name = args[0].__name__
        else:
            module_name = args[0].__name__
    except Exception:
        log.debug("Failed to determine module name when calling `reload`: %r", args, exc_info=True)

    return exec_and_call_hooks(module_name, wrapped, args, kwargs)


def wrapped_find_and_load_unlocked(wrapped, instance, args, kwargs):
    """
    Wrapper for `importlib._bootstrap._find_and_load_unlocked` so we can trigger
      hooks on module loading

    NOTE: This code does not get called for module reloading
    """
    module_name = None
    try:
        module_name = args[0]
    except Exception:
        log.debug("Failed to determine module name when importing module: %r", args, exc_info=True)
        return wrapped(*args, **kwargs)

    return exec_and_call_hooks(module_name, wrapped, args, kwargs)


def wrapped_import(*args, **kwargs):
    """
    Wrapper for `__import__` so we can trigger hooks on module loading
    """
    module_name = kwargs.get("name", args[0])

    # Do not call the hooks every time `import <module>` is called,
    #   only on the first time it is loaded
    if module_name and module_name not in sys.modules:
        return exec_and_call_hooks(module_name, ORIGINAL_IMPORT, args, kwargs)

    return ORIGINAL_IMPORT(*args, **kwargs)


# Keep track of whether we have patched or not
_patched = False


def _patch():
    # Only patch once
    global _patched
    if _patched:
        return

    # 3.x
    if PY3:
        # 3.4: https://github.com/python/cpython/blob/3.4/Lib/importlib/_bootstrap.py#L2207-L2231
        # 3.5: https://github.com/python/cpython/blob/3.5/Lib/importlib/_bootstrap.py#L938-L962
        # 3.6: https://github.com/python/cpython/blob/3.6/Lib/importlib/_bootstrap.py#L936-L960
        # 3.7: https://github.com/python/cpython/blob/3.7/Lib/importlib/_bootstrap.py#L948-L972
        # 3.8: https://github.com/python/cpython/blob/3.8/Lib/importlib/_bootstrap.py#L956-L980
        # DEV: Python 3 has multiple entrypoints for importing a module, but `_find_and_load_unlocked`
        #   is a common base/required function needed by anyone to import a module.
        #   e.g. `__import__` which calls `importlib._bootstrap._find_and_load()`
        #        `importlib.__import__/importlib._bootstrap.__import__` which calls `importlib._bootstrap._gcd_import()`
        #        `importlib.import_module` which calls `importliob._bootstrap._gcd_import()`
        wrapt.wrap_function_wrapper("importlib._bootstrap", "_find_and_load_unlocked", wrapped_find_and_load_unlocked)

        # 3.4: https://github.com/python/cpython/blob/3.4/Lib/importlib/__init__.py#L115-L156
        # 3.5: https://github.com/python/cpython/blob/3.5/Lib/importlib/__init__.py#L132-L173
        # 3.6: https://github.com/python/cpython/blob/3.6/Lib/importlib/__init__.py#L132-L173
        # 3.7: https://github.com/python/cpython/blob/3.7/Lib/importlib/__init__.py#L133-L176
        # 3.8: https://github.com/python/cpython/blob/3.8/Lib/importlib/__init__.py#L133-L176
        wrapt.wrap_function_wrapper("importlib", "reload", wrapped_reload)

    # 2.7
    # DEV: Slightly more direct approach of patching `__import__` and `reload` functions
    elif sys.version_info >= (2, 7):
        # https://github.com/python/cpython/blob/2.7/Python/bltinmodule.c#L35-L68
        if __builtins__["__import__"] is not wrapped_import:
            global ORIGINAL_IMPORT
            ORIGINAL_IMPORT = __builtins__["__import__"]
            __builtins__["__import__"] = wrapped_import

        # https://github.com/python/cpython/blob/2.7/Python/bltinmodule.c#L2147-L2160
        __builtins__["reload"] = wrapt.FunctionWrapper(__builtins__["reload"], wrapped_reload)

    # Update after we have successfully patched
    _patched = True


# DEV: This is called at the end of this module to ensure we always patch
def patch():
    """
    Patch Python import system, enabling import hooks
    """
    # This should never cause their application to not load
    try:
        _patch()
    except Exception:
        log.warning("Failed to patch module importing, import hooks will not work", exc_info=True)


def unpatch():
    """
    Unpatch Python import system, disabling import hooks
    """
    # Only patch once
    global _patched
    if not _patched:
        return
    _patched = False

    # 3.4 -> 3.8
    # DEV: Explicitly stop at 3.8 in case the functions we are patching change in any way,
    #      we need to validate them before adding support here
    if (3, 4) <= sys.version_info <= (3, 8):
        import importlib

        if isinstance(importlib._bootstrap._find_and_load_unlocked, wrapt.FunctionWrapper):
            setattr(
                importlib._bootstrap,
                "_find_and_load_unlocked",
                importlib._bootstrap._find_and_load_unlocked.__wrapped__,
            )
        if isinstance(importlib.reload, wrapt.FunctionWrapper):
            setattr(importlib, "reload", importlib.reload.__wrapped__)

    # 2.7
    # DEV: Slightly more direct approach
    elif sys.version_info >= (2, 7):
        __builtins__["__import__"] = ORIGINAL_IMPORT
        if isinstance(__builtins__["reload"], wrapt.FunctionWrapper):
            __builtins__["reload"] = __builtins__["reload"].__wrapped__


def register_module_hook(module_name, func=None, registry=hooks):
    """
    Register a function as a module import hook

    .. code:: python

        @register_module_hook('requests')
        def requests_hook(requests_module):
            pass


        def requests_hook(requests_module):
            pass


        register_module_hook('requests', requests_hook)


    :param module_name: The name of the module to add a hook for (e.g. 'requests', 'flask.app', etc)
    :type module_name: str
    :param func: The hook function to call when the ``module_name`` is imported
    :type func: function(module)
    :param registry: The hook registry to add this hook to (default: default global registry)
    :type registry: :class:`ModuleHookRegistry`
    :returns: Either a decorator function if ``func`` is not provided, or else the original function
    :rtype: func

    """
    # If they did not give us a function, then return a decorator function
    if not func:

        def wrapper(func):
            return register_module_hook(module_name, func, registry=registry)

        return wrapper

    # Register this function as an import hook
    registry.register(module_name, func)
    return func
