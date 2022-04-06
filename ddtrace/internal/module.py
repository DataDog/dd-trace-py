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
from typing import TYPE_CHECKING
from typing import Tuple
from typing import Union

from bytecode import Bytecode
from bytecode import Instr

from ddtrace.internal.compat import PY2
from ddtrace.internal.logger import get_logger


if TYPE_CHECKING and not PY2:
    from importlib.abc import Loader

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
# https://github.com/GrahamDumpleton/wrapt/blob/0baff1b6ac8d64ffb08ea2d1610c7ed90115b9d5/src/wrapt/importer.py
class _ImportHookChainedLoader:
    def __init__(self, loader, callback):
        # type: (Loader, Callable[[ModuleType], None]) -> None
        self.loader = loader
        self.callback = callback

    def load_module(self, fullname):
        # type: (str) -> ModuleType
        module = self.loader.load_module(fullname)
        self.callback(module)

        return module

    def get_code(self, mod_name):
        return self.loader.get_code(mod_name)


class ModuleWatchdog(dict):
    """Module watchdog.

    Replace the standard ``sys.modules`` dictionary to detect when modules are
    loaded/unloaded. This is also responsible for triggering any registered
    import hooks.
    """

    _run_code = None

    def __init__(self):
        # type: () -> None
        self._hook_map = defaultdict(list)  # type: DefaultDict[str, List[Tuple[HookType, Any]]]
        self._origin_map = {origin(module): module for module in sys.modules.values()}
        self._modules = sys.modules
        self._finding = set()  # type: Set[str]

    def __enter__(self):
        # type: () -> ModuleWatchdog
        self._add_to_meta_path()
        sys.modules = self
        log.debug("%s installed", type(self))
        return self

    def __exit__(self, *exc):
        # type: (Tuple[Any]) -> None
        self._remove_from_meta_path()
        sys.modules = self._modules
        log.debug("%s uninstalled", type(self))

    def __getitem__(self, item):
        # type: (str) -> ModuleType
        return self._modules.__getitem__(item)

    def __setitem__(self, name, module):
        # type: (str, ModuleType) -> None
        self._modules.__setitem__(name, module)

    def _add_to_meta_path(self):
        # type: () -> None
        sys.meta_path.insert(0, self)

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
                return self

            try:
                import importlib.util

                spec = importlib.util.find_spec(fullname)
                loader = spec.loader if spec is not None else None
            except (ImportError, AttributeError):
                loader = importlib.find_loader(fullname, path)
            if loader:
                return _ImportHookChainedLoader(loader, self.after_import)

        finally:
            self._finding.remove(fullname)

        return None

    def load_module(self, fullname):
        # type: (str) -> ModuleType
        # Python 2 only
        module = sys.modules[fullname]
        self.after_import(module)

        return module

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
    def install(cls, on_run_module=False):
        # type: (bool) -> None
        """Install the module watchdog.

        If `on_run_module` is True, then the watchdog will run hooks for the
        module that is currently being run.
        """
        sys.modules = cls()
        sys.modules._add_to_meta_path()
        log.debug("%s installed", cls)

        if on_run_module:
            # If the module is being exeecuted with -m, we patch runpy to catch
            # the moment when the module is loaded.
            import runpy

            cls._run_code = runpy._run_code  # type: ignore[attr-defined]

            def _run_module_hook():
                # type: () -> None
                # The module is loaded in the __main__ namespace. We need to
                # compute its actual origin as this is not stored by the loader.
                module = sys.modules["__main__"]
                assert module.__spec__ is not None, "Main module '%r' has spec" % module
                module_name = module.__spec__.name
                module_path_base = abspath(join(*module_name.split(".")))

                def _test_py_file(base_path):
                    if isfile(base_path + ".py"):
                        return base_path + ".py"
                    return None

                # Check if it is a .py file
                module_origin = _test_py_file(module_path_base)
                if module_origin is None:
                    # If not, check if it is a __main__.py file.
                    module_origin = _test_py_file(join(module_path_base, "__main__"))

                if module_origin is not None:
                    # Register the module origin mapping
                    sys.modules._origin_map[module_origin] = module  # type: ignore[attr-defined]

                    # Temporarily override the __file__ attribute to allow
                    # any registered hooks to run.
                    old_file = module.__file__
                    module.__file__ = module_origin
                    try:
                        sys.modules.after_import(module)  # type: ignore[attr-defined]
                    finally:
                        module.__file__ = old_file

            def _(*args, **kwargs):
                abstract_code = Bytecode.from_code(args[0])
                for i, instr in enumerate(abstract_code):
                    if not isinstance(instr, Instr):
                        continue

                    if instr.name == "MAKE_FUNCTION" and abstract_code[i + 1].name == "STORE_NAME":
                        # DEV: This is a bit dumb. Every time we find a function
                        # definition, we re-trigger the hooks to see if there is
                        # a new function to patch. This code is unlikely to be
                        # exercised in practice, but it is important to be aware
                        # of this behaviour.
                        lineno = abstract_code[i + 1].lineno
                        abstract_code[i + 2 : i + 2] = Bytecode(
                            [
                                Instr("LOAD_CONST", _run_module_hook, lineno=lineno),
                                Instr("CALL_FUNCTION", 0, lineno=lineno),
                                Instr("POP_TOP", lineno=lineno),
                            ]
                        )

                args[1][_run_module_hook.__name__] = _run_module_hook
                args = (abstract_code.to_code(),) + args[1:]
                return cls._run_code(*args, **kwargs)

            runpy._run_code = _  # type: ignore[attr-defined]

    @classmethod
    def is_installed(cls):
        """Check whether this module watchdog class is installed."""
        return any(type(_) is cls for _ in sys.meta_path)

    @classmethod
    def uninstall(cls):
        # type: () -> None
        """Uninstall the module watchdog."""
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
