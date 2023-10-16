from collections import defaultdict
from os.path import abspath
from os.path import expanduser
from os.path import isdir
from os.path import isfile
from os.path import join
import sys
import typing
from typing import cast


if typing.TYPE_CHECKING:
    from types import ModuleType
    from typing import Any
    from typing import Callable
    from typing import DefaultDict
    from typing import Dict
    from typing import List
    from typing import Optional
    from typing import Set
    from typing import Tuple
    from typing import Type
    from typing import Union

    ModuleHookType = Callable[[ModuleType], None]
    PreExecHookType = Callable[[Any, ModuleType], None]
    PreExecHookCond = Union[str, Callable[[str], bool]]

from ddtrace.internal.compat import PY2
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value


if PY2:
    wvdict = dict
else:
    from weakref import WeakValueDictionary as wvdict

log = get_logger(__name__)


_run_code = None
_post_run_module_hooks = []  # type: List[ModuleHookType]


def _wrapped_run_code(*args, **kwargs):
    # type: (*Any, **Any) -> Dict[str, Any]
    global _run_code, _post_run_module_hooks

    # DEV: If we are calling this wrapper then _run_code must have been set to
    # the original runpy._run_code.
    assert _run_code is not None

    mod_name = get_argument_value(args, kwargs, 3, "mod_name")

    try:
        return _run_code(*args, **kwargs)
    finally:
        module = sys.modules[mod_name]
        for hook in _post_run_module_hooks:
            hook(module)


def _patch_run_code():
    # type: () -> None
    global _run_code

    if _run_code is None:
        import runpy

        _run_code = runpy._run_code  # type: ignore[attr-defined]
        runpy._run_code = _wrapped_run_code  # type: ignore[attr-defined]


def register_post_run_module_hook(hook):
    # type: (ModuleHookType) -> None
    """Register a post run module hook.

    The hooks gets called after the module is loaded. For this to work, the
    hook needs to be registered during the interpreter initialization, e.g. as
    part of a sitecustomize.py script.
    """
    global _run_code, _post_run_module_hooks

    _patch_run_code()

    _post_run_module_hooks.append(hook)


def unregister_post_run_module_hook(hook):
    # type: (ModuleHookType) -> None
    """Unregister a post run module hook.

    If the hook was not registered, a ``ValueError`` exception is raised.
    """
    global _post_run_module_hooks

    _post_run_module_hooks.remove(hook)


def origin(module):
    # type: (ModuleType) -> str
    """Get the origin source file of the module."""
    try:
        # DEV: Use object.__getattribute__ to avoid potential side-effects.
        orig = abspath(object.__getattribute__(module, "__file__"))
    except (AttributeError, TypeError):
        # Module is probably only partially initialised, so we look at its
        # spec instead
        try:
            # DEV: Use object.__getattribute__ to avoid potential side-effects.
            orig = abspath(object.__getattribute__(module, "__spec__").origin)
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
    import pkgutil

    find_spec = ModuleSpec = None
    Loader = object

    find_loader = pkgutil.find_loader

else:
    from importlib.abc import Loader
    from importlib.machinery import ModuleSpec
    from importlib.util import find_spec

    def find_loader(fullname):
        # type: (str) -> Optional[Loader]
        return getattr(find_spec(fullname), "loader", None)


LEGACY_DICT_COPY = sys.version_info < (3, 6)


class _ImportHookChainedLoader(Loader):
    def __init__(self, loader):
        # type: (Loader) -> None
        self.loader = loader
        self.callbacks = {}  # type: Dict[Any, Callable[[ModuleType], None]]

        # DEV: load_module is deprecated so we define it at runtime if also
        # defined by the default loader. We also check and define for the
        # methods that are supposed to replace the load_module functionality.
        if hasattr(loader, "load_module"):
            self.load_module = self._load_module  # type: ignore[assignment]
        if hasattr(loader, "create_module"):
            self.create_module = self._create_module  # type: ignore[assignment]
        if hasattr(loader, "exec_module"):
            self.exec_module = self._exec_module  # type: ignore[assignment]

    def __getattribute__(self, name):
        if name == "__class__":
            # Make isinstance believe that self is also an instance of
            # type(self.loader). This is required, e.g. by some tools, like
            # slotscheck, that can handle known loaders only.
            return self.loader.__class__

        return super(_ImportHookChainedLoader, self).__getattribute__(name)

    def __getattr__(self, name):
        # Proxy any other attribute access to the underlying loader.
        return getattr(self.loader, name)

    def add_callback(self, key, callback):
        # type: (Any, Callable[[ModuleType], None]) -> None
        self.callbacks[key] = callback

    def _load_module(self, fullname):
        # type: (str) -> ModuleType
        module = self.loader.load_module(fullname)
        for callback in self.callbacks.values():
            callback(module)

        return module

    def _create_module(self, spec):
        return self.loader.create_module(spec)

    def _exec_module(self, module):
        # Collect and run only the first hook that matches the module.
        pre_exec_hook = None

        for _ in sys.meta_path:
            if isinstance(_, ModuleWatchdog):
                try:
                    for cond, hook in _._pre_exec_module_hooks:
                        if isinstance(cond, str) and cond == module.__name__ or cond(module.__name__):
                            # Several pre-exec hooks could match, we keep the first one
                            pre_exec_hook = hook
                            break
                except Exception:
                    log.debug("Exception happened while processing pre_exec_module_hooks", exc_info=True)

            if pre_exec_hook is not None:
                break

        if pre_exec_hook:
            pre_exec_hook(self, module)
        else:
            self.loader.exec_module(module)

        for callback in self.callbacks.values():
            callback(module)


class ModuleWatchdog(object):
    """Module watchdog.

    Hooks into the import machinery to detect when modules are loaded/unloaded.
    This is also responsible for triggering any registered import hooks.

    Subclasses might customize the default behavior by overriding the
    ``after_import`` method, which is triggered on every module import, once
    the subclass is installed.
    """

    _instance = None  # type: Optional[ModuleWatchdog]

    def __init__(self):
        # type: () -> None
        self._hook_map = defaultdict(list)  # type: DefaultDict[str, List[ModuleHookType]]
        self._om = None  # type: Optional[wvdict[str, ModuleType]]
        self._finding = set()  # type: Set[str]
        self._pre_exec_module_hooks = []  # type: List[Tuple[PreExecHookCond, PreExecHookType]]

    @property
    def _origin_map(self):
        # type: () -> wvdict[str, ModuleType]
        if self._om is None:
            try:
                self._om = wvdict({origin(module): module for module in sys.modules.values()})
            except RuntimeError:
                # The state of sys.modules might have been mutated by another
                # thread. We try to build the full mapping at the next occasion.
                # For now we take the more expensive route of building a list of
                # the current values, which might be incomplete.
                return wvdict({origin(module): module for module in list(sys.modules.values())})

        return self._om

    def _add_to_meta_path(self):
        # type: () -> None
        sys.meta_path.insert(0, self)  # type: ignore[arg-type]

    @classmethod
    def _find_in_meta_path(cls):
        # type: () -> Optional[int]
        for i, meta_path in enumerate(sys.meta_path):
            if type(meta_path) is cls:
                return i
        return None

    @classmethod
    def _remove_from_meta_path(cls):
        # type: () -> None
        i = cls._find_in_meta_path()

        if i is None:
            raise RuntimeError("%s is not installed" % cls.__name__)

        sys.meta_path.pop(i)

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
            for hook in hooks:
                hook(module)

    @classmethod
    def get_by_origin(cls, _origin):
        # type: (str) -> Optional[ModuleType]
        """Lookup a module by its origin."""
        cls._check_installed()

        instance = cast(ModuleWatchdog, cls._instance)

        path = _resolve(_origin)
        if path is not None:
            module = instance._origin_map.get(path)
            if module is not None:
                return module

            # Check if this is the __main__ module
            main_module = sys.modules.get("__main__")
            if main_module is not None and origin(main_module) == path:
                # Register for future lookups
                instance._origin_map[path] = main_module

                return main_module

        return None

    def find_module(self, fullname, path=None):
        # type: (str, Optional[str]) -> Union[Loader, None]
        if fullname in self._finding:
            return None

        self._finding.add(fullname)

        try:
            loader = find_loader(fullname)
            if loader is not None:
                if not isinstance(loader, _ImportHookChainedLoader):
                    loader = _ImportHookChainedLoader(loader)

                if PY2:
                    # With Python 2 we don't get all the finders invoked, so we
                    # make sure we register all the callbacks at the earliest
                    # opportunity.
                    for finder in sys.meta_path:
                        if isinstance(finder, ModuleWatchdog):
                            loader.add_callback(type(finder), finder.after_import)
                else:
                    loader.add_callback(type(self), self.after_import)

                return loader

        finally:
            self._finding.remove(fullname)

        return None

    def find_spec(self, fullname, path=None, target=None):
        # type: (str, Optional[str], Optional[ModuleType]) -> Optional[ModuleSpec]
        if fullname in self._finding:
            return None

        self._finding.add(fullname)

        try:
            try:
                # Best effort
                spec = find_spec(fullname)
            except Exception:
                return None

            if spec is None:
                return None

            loader = getattr(spec, "loader", None)

            if loader is not None:
                if not isinstance(loader, _ImportHookChainedLoader):
                    spec.loader = _ImportHookChainedLoader(loader)

                cast(_ImportHookChainedLoader, spec.loader).add_callback(type(self), self.after_import)

            return spec

        finally:
            self._finding.remove(fullname)

    @classmethod
    def register_origin_hook(cls, origin, hook):
        # type: (str, ModuleHookType) -> None
        """Register a hook to be called when the module with the given origin is
        imported.

        The hook will be called with the module object as argument.
        """
        cls._check_installed()

        # DEV: Under the hypothesis that this is only ever called by the probe
        # poller thread, there are no further actions to take. Should this ever
        # change, then thread-safety might become a concern.
        path = _resolve(origin)
        if path is None:
            raise ValueError("Cannot resolve module origin %s" % origin)

        log.debug("Registering hook '%r' on path '%s'", hook, path)
        instance = cast(ModuleWatchdog, cls._instance)
        instance._hook_map[path].append(hook)
        try:
            module = instance._origin_map[path]
            # Sanity check: the module might have been removed from sys.modules
            # but not yet garbage collected.
            try:
                sys.modules[module.__name__]
            except KeyError:
                del instance._origin_map[path]
                raise
        except KeyError:
            # The module is not loaded yet. Nothing more we can do.
            return

        # The module was already imported so we invoke the hook straight-away
        log.debug("Calling hook '%r' on already imported module '%s'", hook, module.__name__)
        hook(module)

    @classmethod
    def unregister_origin_hook(cls, origin, hook):
        # type: (str, ModuleHookType) -> None
        """Unregister the hook registered with the given module origin and
        argument.
        """
        cls._check_installed()

        path = _resolve(origin)
        if path is None:
            raise ValueError("Module origin %s cannot be resolved", origin)

        instance = cast(ModuleWatchdog, cls._instance)
        if path not in instance._hook_map:
            raise ValueError("No hooks registered for origin %s" % origin)

        try:
            if path in instance._hook_map:
                hooks = instance._hook_map[path]
                hooks.remove(hook)
                if not hooks:
                    del instance._hook_map[path]
        except ValueError:
            raise ValueError("Hook %r not registered for origin %s" % (hook, origin))

    @classmethod
    def register_module_hook(cls, module, hook):
        # type: (str, ModuleHookType) -> None
        """Register a hook to be called when the module with the given name is
        imported.

        The hook will be called with the module object as argument.
        """
        cls._check_installed()

        log.debug("Registering hook '%r' on module '%s'", hook, module)
        instance = cast(ModuleWatchdog, cls._instance)
        instance._hook_map[module].append(hook)
        try:
            module_object = sys.modules[module]
        except KeyError:
            # The module is not loaded yet. Nothing more we can do.
            return

        # The module was already imported so we invoke the hook straight-away
        log.debug("Calling hook '%r' on already imported module '%s'", hook, module)
        hook(module_object)

    @classmethod
    def unregister_module_hook(cls, module, hook):
        # type: (str, ModuleHookType) -> None
        """Unregister the hook registered with the given module name and
        argument.
        """
        cls._check_installed()

        instance = cast(ModuleWatchdog, cls._instance)
        if module not in instance._hook_map:
            raise ValueError("No hooks registered for module %s" % module)

        try:
            if module in instance._hook_map:
                hooks = instance._hook_map[module]
                hooks.remove(hook)
                if not hooks:
                    del instance._hook_map[module]
        except ValueError:
            raise ValueError("Hook %r not registered for module %r" % (hook, module))

    @classmethod
    def after_module_imported(cls, module):
        # type: (str) -> Callable[[ModuleHookType], None]
        def _(hook):
            # type: (ModuleHookType) -> None
            cls.register_module_hook(module, hook)

        return _

    @classmethod
    def register_pre_exec_module_hook(cls, cond, hook):
        # type: (Type[ModuleWatchdog], PreExecHookCond, PreExecHookType) -> None
        """Register a hook to execute before/instead of exec_module.

        The pre exec_module hook is executed before the module is executed
        to allow for changed modules to be executed as needed. To ensure
        that the hook is applied only to the modules that are required,
        the condition is evaluated against the module name.
        """
        cls._check_installed()

        log.debug("Registering pre_exec module hook '%r' on condition '%s'", hook, cond)
        instance = cast(ModuleWatchdog, cls._instance)
        instance._pre_exec_module_hooks.append((cond, hook))

    @classmethod
    def _check_installed(cls):
        # type: () -> None
        if not cls.is_installed():
            raise RuntimeError("%s is not installed" % cls.__name__)

    @classmethod
    def install(cls):
        # type: () -> None
        """Install the module watchdog."""
        if cls.is_installed():
            raise RuntimeError("%s is already installed" % cls.__name__)

        cls._instance = cls()
        cls._instance._add_to_meta_path()
        log.debug("%s installed", cls)

    @classmethod
    def is_installed(cls):
        """Check whether this module watchdog class is installed."""
        return cls._instance is not None and type(cls._instance) is cls

    @classmethod
    def uninstall(cls):
        # type: () -> None
        """Uninstall the module watchdog.

        This will uninstall only the most recently installed instance of this
        class.
        """
        cls._check_installed()
        cls._remove_from_meta_path()

        cls._instance = None

        log.debug("%s uninstalled", cls)
