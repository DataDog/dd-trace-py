from collections import defaultdict
from os.path import abspath
from os.path import expanduser
from os.path import isdir
from os.path import isfile
from os.path import join
import sys
from types import ModuleType
from typing import Any
from typing import Callable
from typing import DefaultDict
from typing import Iterator
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
from typing import Union

from ddtrace.internal.compat import PY2
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

HookType = Callable[[ModuleType, Any], None]


def origin(module):
    # type: (ModuleType) -> str
    """Get the origin of the module."""
    try:
        orig = abspath(module.__file__)  # type: ignore[type-var]
    except (AttributeError, TypeError):
        # Module is probably only partially initialised, so we look at its
        # spec instead
        try:
            orig = abspath(module.__spec__.origin)  # type: ignore
        except (AttributeError, ValueError, TypeError):
            orig = None

    if orig is not None and isfile(orig):
        if orig.endswith(".pyc"):
            orig = orig[:-1]
        return orig

    return "<unknown origin>"


def _resolve(path):
    # type: (str) -> Optional[str]
    """Resolve a (relative) path with respect to sys.path."""
    for base in sys.path:
        if isdir(base):
            resolved_path = abspath(join(base, expanduser(path)))
            if isfile(resolved_path):
                return resolved_path
    return None


# Borrowed from the wrapt module
# https://github.com/GrahamDumpleton/wrapt/blob/df0e62c2740143cceb6cafea4c306dae1c559ef8/src/wrapt/importer.py

if PY2:
    find_spec = ModuleSpec = None
    Loader = object
else:
    from importlib.abc import Loader
    from importlib.machinery import ModuleSpec
    from importlib.util import find_spec


# DEV: This is used by Python 2 only
class _ImportHookLoader(object):
    def __init__(self, callback):
        # type: (Callable[[ModuleType], None]) -> None
        self.callback = callback

    def load_module(self, fullname):
        # type: (str) -> ModuleType
        module = sys.modules[fullname]
        self.callback(module)

        return module


class _ImportHookChainedLoader(Loader):
    def __init__(self, loader, callback):
        # type: (Loader, Callable[[ModuleType], None]) -> None
        self.loader = loader
        self.callback = callback

        # DEV: load_module is deprecated so we define it at runtime if also
        # defined by the default loader. We also check and define for the
        # methods that are supposed to replace the load_module functionality.
        if hasattr(loader, "load_module"):
            self.load_module = self._load_module  # type: ignore[assignment]
        if hasattr(loader, "create_module"):
            self.create_module = self._create_module  # type: ignore[assignment]
        if hasattr(loader, "exec_module"):
            self.exec_module = self._exec_module  # type: ignore[assignment]

    def _load_module(self, fullname):
        # type: (str) -> ModuleType
        module = self.loader.load_module(fullname)
        self.callback(module)

        return module

    def _create_module(self, spec):
        return self.loader.create_module(spec)

    def _exec_module(self, module):
        self.loader.exec_module(module)
        self.callback(sys.modules[module.__name__])

    def get_code(self, mod_name):
        return self.loader.get_code(mod_name)


class ModuleWatchdog(dict):
    """Module watchdog.

    Replace the standard ``sys.modules`` dictionary to detect when modules are
    loaded/unloaded. This is also responsible for triggering any registered
    import hooks.

    Subclasses might customize the default behavior by overriding the
    ``after_import`` method, which is triggered on every module import, once
    the subclass is installed.
    """

    _run_code = None

    def __init__(self):
        # type: () -> None
        self._hook_map = defaultdict(list)  # type: DefaultDict[str, List[Tuple[HookType, Any]]]
        self._origin_map = {origin(module): module for module in sys.modules.values()}
        self._modules = sys.modules  # type: Union[dict, ModuleWatchdog]
        self._finding = set()  # type: Set[str]

    def __getitem__(self, item):
        # type: (str) -> ModuleType
        return self._modules.__getitem__(item)

    def __setitem__(self, name, module):
        # type: (str, ModuleType) -> None
        self._modules.__setitem__(name, module)

    def _add_to_meta_path(self):
        # type: () -> None
        sys.meta_path.insert(0, self)  # type: ignore[arg-type]

    def _remove_from_meta_path(self):
        # type: () -> None
        for i, meta_path in enumerate(sys.meta_path):
            if type(meta_path) is type(self):
                sys.meta_path.pop(i)
                return

    def after_import(self, module):
        # type: (ModuleType) -> None
        path = origin(module)
        self._origin_map[path] = module

        # Collect all hooks by module origin and name
        hooks = []
        if path in self._hook_map:
            hooks.extend(self._hook_map[path])
        if module.__name__ in self._hook_map:
            hooks.extend(self._hook_map[module.__name__])

        if hooks:
            log.debug("Calling %d registered hooks on import of module '%s'", len(hooks), module.__name__)
            for hook, arg in hooks:
                hook(module, arg)

    def get_by_origin(self, origin):
        # type: (str) -> Optional[ModuleType]
        """Lookup a module by its origin."""
        path = _resolve(origin)
        if path is not None:
            return self._origin_map.get(path)
        return None

    def __delitem__(self, name):
        # type: (str) -> None
        try:
            path = origin(sys.modules[name])
            # Drop the module reference to reclaim memory
            del self._origin_map[path]
        except KeyError:
            pass

        self._modules.__delitem__(name)

    def __getattribute__(self, name):
        # type: (str) -> Any
        try:
            return super(ModuleWatchdog, self).__getattribute__("_modules").__getattribute__(name)
        except AttributeError:
            return super(ModuleWatchdog, self).__getattribute__(name)

    def __contains__(self, name):
        # type: (object) -> bool
        return self._modules.__contains__(name)

    def __len__(self):
        # type: () -> int
        return self._modules.__len__()

    def __iter__(self):
        # type: () -> Iterator
        return self._modules.__iter__()

    def find_module(self, fullname, path=None):
        # type: (str, Optional[str]) -> Union[ModuleWatchdog, _ImportHookChainedLoader, None]
        if fullname in self._finding:
            return None

        self._finding.add(fullname)

        try:
            if PY2:
                __import__(fullname)
                return _ImportHookLoader(self.after_import)

            loader = getattr(find_spec(fullname), "loader", None)
            if loader and not isinstance(loader, _ImportHookChainedLoader):
                return _ImportHookChainedLoader(loader, self.after_import)

        finally:
            self._finding.remove(fullname)

        return None

    def find_spec(self, fullname, path=None, target=None):
        # type: (str, Optional[str], Optional[ModuleType]) -> Optional[ModuleSpec]
        if fullname in self._finding:
            return None

        self._finding.add(fullname)

        try:
            spec = find_spec(fullname)
            if spec is None:
                return None

            loader = getattr(spec, "loader", None)

            if loader and not isinstance(loader, _ImportHookChainedLoader):
                spec.loader = _ImportHookChainedLoader(loader, self.after_import)

            return spec

        finally:
            self._finding.remove(fullname)

    @classmethod
    def register_origin_hook(cls, origin, hook, arg):
        # type: (str, HookType, Any) -> None
        """Register a hook to be called when the module with the given origin is
        imported.

        The hook will be called with the module object and the given argument.
        """
        assert isinstance(sys.modules, cls), "%r is installed" % cls

        # DEV: Under the hypothesis that this is only ever called by the probe
        # poller thread, there are no further actions to take. Should this ever
        # change, then thread-safety might become a concern.
        path = _resolve(origin)
        if path is None:
            raise ValueError("Cannot resolve module origin %s" % origin)

        log.debug("Registering hook '%r' on path '%s'", hook, path)
        sys.modules._hook_map[path].append((hook, arg))
        try:
            module = sys.modules._origin_map[path]
        except KeyError:
            # The module is not loaded yet. Nothing more we can do.
            return

        # The module was already imported so we invoke the hook straight-away
        log.debug("Calling hook '%r' on already imported module '%s'", hook, module.__name__)
        hook(module, arg)

    @classmethod
    def unregister_origin_hook(cls, origin, arg):
        # type: (str, Any) -> None
        """Unregister the hook registered with the given module origin and
        argument.
        """
        assert isinstance(sys.modules, cls), "%r is installed" % cls

        path = _resolve(origin)
        if path is None:
            raise ValueError("Module origin %s cannot be resolved", origin)

        if path not in sys.modules._hook_map:
            raise ValueError("No hooks registered for origin %s" % origin)

        for i, (_, a) in enumerate(sys.modules._hook_map[path]):
            if isinstance(a, list) and arg in a:
                # DEV: This comes from the knowledge that sometimes the argument
                # of the hook can be a list.
                a.remove(arg)
                if not a:
                    sys.modules._hook_map[path].pop(i)
                return
            elif a is arg:
                sys.modules._hook_map[path].pop(i)
                return
        raise ValueError("No hook registered for origin %s with argument %r" % (origin, arg))

    @classmethod
    def register_module_hook(cls, module, hook, arg):
        # type: (str, HookType, Any) -> None
        """Register a hook to be called when the module with the given name is
        imported.

        The hook will be called with the module object and the given argument.
        """
        assert isinstance(sys.modules, cls), "%r is installed" % cls

        log.debug("Registering hook '%r' on module '%s'", hook, module)
        sys.modules._hook_map[module].append((hook, arg))
        try:
            module_object = sys.modules[module]
        except KeyError:
            # The module is not loaded yet. Nothing more we can do.
            return

        # The module was already imported so we invoke the hook straight-away
        log.debug("Calling hook '%r' on already imported module '%s'", hook, module)
        hook(module_object, arg)

    @classmethod
    def unregister_module_hook(cls, module, arg):
        # type: (str, Any) -> None
        """Unregister the hook registered with the given module name and
        argument.
        """
        assert isinstance(sys.modules, cls), "%r is installed" % cls

        if module not in sys.modules._hook_map:
            raise ValueError("No hooks registered for module %s" % module)

        for i, (_, a) in enumerate(sys.modules._hook_map[module]):
            if isinstance(a, list) and arg in a:
                # DEV: This comes from the knowledge that sometimes the argument
                # of the hook can be a list.
                a.remove(arg)
                if not a:
                    sys.modules._hook_map[module].pop(i)
                return
            elif a is arg:
                sys.modules._hook_map[module].pop(i)
                return
        raise ValueError("No hook registered for module %s with argument %r" % (module, arg))

    @classmethod
    def install(cls, on_run_module=None):
        # type: (Optional[Callable[[Callable[..., Any]], Callable[..., Any]]]) -> None
        """Install the module watchdog.

        The optional `on_run_module` is a callable that implements a wrapper
        around runpy._run_code. This can be passed in to catch when a module is
        being loaded as a consequence of running it with the -m flag. For this
        to work, the install method needs to be called in, e.g., a
        custom sitecustomize.py file.
        """
        sys.modules = cls()
        sys.modules._add_to_meta_path()
        log.debug("%s installed", cls)

        if on_run_module:
            # If the module is being exeecuted with -m, we patch runpy to catch
            # the moment when the module is loaded.
            import runpy

            # We store the original function
            cls._run_code = runpy._run_code  # type: ignore[attr-defined]

            runpy._run_code = on_run_module(cls._run_code)  # type: ignore[attr-defined]

    @classmethod
    def is_installed(cls):
        """Check whether this module watchdog class is installed."""
        return any(type(_) is cls for _ in sys.meta_path)

    @classmethod
    def uninstall(cls):
        # type: () -> None
        """Uninstall the module watchdog.

        This will uninstall only the most recently installed instance of this
        class.
        """
        parent, current = None, sys.modules
        while isinstance(current, ModuleWatchdog):
            if type(current) is cls:
                current._remove_from_meta_path()
                if parent is not None:
                    setattr(parent, "_modules", getattr(current, "_modules"))
                else:
                    sys.modules = getattr(current, "_modules")
                log.debug("ModuleWatchdog uninstalled")
                return
            parent = current
            current = current._modules
