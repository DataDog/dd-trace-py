import abc
from collections import defaultdict
from importlib._bootstrap import _init_module_attrs
from importlib.abc import Loader
from importlib.machinery import ModuleSpec
from importlib.util import find_spec
from pathlib import Path
import sys
from types import ModuleType
import typing as t
from weakref import WeakValueDictionary as wvdict

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value


ModuleHookType = t.Callable[[ModuleType], None]
PreExecHookType = t.Callable[[t.Any, ModuleType], None]
PreExecHookCond = t.Union[str, t.Callable[[str], bool]]


log = get_logger(__name__)


_run_code = None
_post_run_module_hooks: t.List[ModuleHookType] = []


def _wrapped_run_code(*args: t.Any, **kwargs: t.Any) -> t.Dict[str, t.Any]:
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


def _patch_run_code() -> None:
    global _run_code

    if _run_code is None:
        import runpy

        _run_code = runpy._run_code  # type: ignore[attr-defined]
        runpy._run_code = _wrapped_run_code  # type: ignore[attr-defined]


def register_post_run_module_hook(hook: ModuleHookType) -> None:
    """Register a post run module hook.

    The hooks gets called after the module is loaded. For this to work, the
    hook needs to be registered during the interpreter initialization, e.g. as
    part of a sitecustomize.py script.
    """
    global _run_code, _post_run_module_hooks

    _patch_run_code()

    _post_run_module_hooks.append(hook)


def unregister_post_run_module_hook(hook: ModuleHookType) -> None:
    """Unregister a post run module hook.

    If the hook was not registered, a ``ValueError`` exception is raised.
    """
    global _post_run_module_hooks

    _post_run_module_hooks.remove(hook)


def origin(module: ModuleType) -> t.Optional[Path]:
    """Get the origin source file of the module."""
    try:
        # DEV: Use object.__getattribute__ to avoid potential side-effects.
        orig = Path(object.__getattribute__(module, "__file__")).resolve()
    except (AttributeError, TypeError):
        # Module is probably only partially initialised, so we look at its
        # spec instead
        try:
            # DEV: Use object.__getattribute__ to avoid potential side-effects.
            orig = Path(object.__getattribute__(module, "__spec__").origin).resolve()
        except (AttributeError, ValueError, TypeError):
            orig = None

    if orig is not None and orig.is_file():
        return orig.with_suffix(".py") if orig.suffix == ".pyc" else orig

    return None


def _resolve(path: Path) -> t.Optional[Path]:
    """Resolve a (relative) path with respect to sys.path."""
    for base in (Path(_) for _ in sys.path):
        if base.is_dir():
            resolved_path = (base / path.expanduser()).resolve()
            if resolved_path.is_file():
                return resolved_path
    return None


# Borrowed from the wrapt module
# https://github.com/GrahamDumpleton/wrapt/blob/df0e62c2740143cceb6cafea4c306dae1c559ef8/src/wrapt/importer.py


def find_loader(fullname: str) -> t.Optional[Loader]:
    return getattr(find_spec(fullname), "loader", None)


def is_module_installed(module_name):
    return find_loader(module_name) is not None


def is_namespace_spec(spec: ModuleSpec) -> bool:
    return spec.origin is None and spec.submodule_search_locations is not None


class _ImportHookChainedLoader:
    def __init__(self, loader: t.Optional[Loader], spec: t.Optional[ModuleSpec] = None) -> None:
        self.loader = loader
        self.spec = spec

        self.callbacks: t.Dict[t.Any, t.Callable[[ModuleType], None]] = {}

        # A missing loader is generally an indication of a namespace package.
        if loader is None or hasattr(loader, "create_module"):
            self.create_module = self._create_module
        if loader is None or hasattr(loader, "exec_module"):
            self.exec_module = self._exec_module

    def __getattr__(self, name):
        # Proxy any other attribute access to the underlying loader.
        return getattr(self.loader, name)

    def add_callback(self, key: t.Any, callback: t.Callable[[ModuleType], None]) -> None:
        self.callbacks[key] = callback

    def call_back(self, module: ModuleType) -> None:
        if module.__name__ == "pkg_resources":
            # DEV: pkg_resources support to prevent errors such as
            # NotImplementedError: Can't perform this operation for unregistered
            # loader type
            module.register_loader_type(_ImportHookChainedLoader, module.DefaultProvider)

        for callback in self.callbacks.values():
            callback(module)

    def load_module(self, fullname: str) -> t.Optional[ModuleType]:
        if self.loader is None:
            if self.spec is None:
                return None
            sys.modules[self.spec.name] = module = ModuleType(fullname)
            _init_module_attrs(self.spec, module)
        else:
            module = self.loader.load_module(fullname)

        self.call_back(module)

        return module

    def _create_module(self, spec):
        if self.loader is not None:
            return self.loader.create_module(spec)

        if is_namespace_spec(spec):
            module = ModuleType(spec.name)
            _init_module_attrs(spec, module)
            return module

        return None

    def _exec_module(self, module: ModuleType) -> None:
        # Collect and run only the first hook that matches the module.
        pre_exec_hook = None

        for _ in sys.meta_path:
            if isinstance(_, ModuleWatchdog):
                try:
                    for cond, hook in _._pre_exec_module_hooks:
                        if (isinstance(cond, str) and cond == module.__name__) or (
                            callable(cond) and cond(module.__name__)
                        ):
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
            if self.loader is None:
                spec = getattr(module, "__spec__", None)
                if spec is not None and is_namespace_spec(spec):
                    sys.modules[spec.name] = module
            else:
                self.loader.exec_module(module)

        self.call_back(module)


class BaseModuleWatchdog(abc.ABC):
    """Base module watchdog.

    Invokes ``after_import`` every time a new module is imported.
    """

    _instance: t.Optional["BaseModuleWatchdog"] = None

    def __init__(self) -> None:
        self._finding: t.Set[str] = set()

        # DEV: pkg_resources support to prevent errors such as
        # NotImplementedError: Can't perform this operation for unregistered
        pkg_resources = sys.modules.get("pkg_resources")
        if pkg_resources is not None:
            pkg_resources.register_loader_type(_ImportHookChainedLoader, pkg_resources.DefaultProvider)

    def _add_to_meta_path(self) -> None:
        sys.meta_path.insert(0, self)  # type: ignore[arg-type]

    @classmethod
    def _find_in_meta_path(cls) -> t.Optional[int]:
        for i, meta_path in enumerate(sys.meta_path):
            if type(meta_path) is cls:
                return i
        return None

    @classmethod
    def _remove_from_meta_path(cls) -> None:
        i = cls._find_in_meta_path()

        if i is None:
            raise RuntimeError("%s is not installed" % cls.__name__)

        sys.meta_path.pop(i)

    def after_import(self, module: ModuleType) -> None:
        raise NotImplementedError()

    def find_module(self, fullname: str, path: t.Optional[str] = None) -> t.Optional[Loader]:
        if fullname in self._finding:
            return None

        self._finding.add(fullname)

        try:
            original_loader = find_loader(fullname)
            if original_loader is not None:
                loader = (
                    _ImportHookChainedLoader(original_loader)
                    if not isinstance(original_loader, _ImportHookChainedLoader)
                    else original_loader
                )

                loader.add_callback(type(self), self.after_import)

                return t.cast(Loader, loader)

        finally:
            self._finding.remove(fullname)

        return None

    def find_spec(
        self, fullname: str, path: t.Optional[str] = None, target: t.Optional[ModuleType] = None
    ) -> t.Optional[ModuleSpec]:
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

            if not isinstance(loader, _ImportHookChainedLoader):
                spec.loader = t.cast(Loader, _ImportHookChainedLoader(loader, spec))

            t.cast(_ImportHookChainedLoader, spec.loader).add_callback(type(self), self.after_import)

            return spec

        finally:
            self._finding.remove(fullname)

    @classmethod
    def _check_installed(cls) -> None:
        if not cls.is_installed():
            raise RuntimeError("%s is not installed" % cls.__name__)

    @classmethod
    def install(cls) -> None:
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
    def uninstall(cls) -> None:
        """Uninstall the module watchdog.

        This will uninstall only the most recently installed instance of this
        class.
        """
        cls._check_installed()
        cls._remove_from_meta_path()

        cls._instance = None

        log.debug("%s uninstalled", cls)


class ModuleWatchdog(BaseModuleWatchdog):
    """Module watchdog.

    Hooks into the import machinery to detect when modules are loaded/unloaded.
    This is also responsible for triggering any registered import hooks.

    Subclasses might customize the default behavior by overriding the
    ``after_import`` method, which is triggered on every module import, once
    the subclass is installed.
    """

    def __init__(self) -> None:
        super().__init__()

        self._hook_map: t.DefaultDict[str, t.List[ModuleHookType]] = defaultdict(list)
        self._om: t.Optional[t.Dict[str, ModuleType]] = None
        self._pre_exec_module_hooks: t.List[t.Tuple[PreExecHookCond, PreExecHookType]] = []

    @property
    def _origin_map(self) -> t.Dict[str, ModuleType]:
        def modules_with_origin(modules: t.Iterable[ModuleType]) -> t.Dict[str, t.Any]:
            result: wvdict = wvdict()

            for m in modules:
                module_origin = origin(m)
                if module_origin is None:
                    continue

                try:
                    result[str(module_origin)] = m
                except TypeError:
                    # This can happen if the module is a special object that
                    # does not allow for weak references. Quite likely this is
                    # an object created by a native extension. We make the
                    # assumption that this module does not contain valuable
                    # information that can be used at the Python runtime level.
                    pass

            return t.cast(t.Dict[str, t.Any], result)

        if self._om is None:
            try:
                self._om = modules_with_origin(sys.modules.values())
            except RuntimeError:
                # The state of sys.modules might have been mutated by another
                # thread. We try to build the full mapping at the next occasion.
                # For now we take the more expensive route of building a list of
                # the current values, which might be incomplete.
                return modules_with_origin(list(sys.modules.values()))
        return self._om

    def after_import(self, module: ModuleType) -> None:
        module_path = origin(module)
        path = str(module_path) if module_path is not None else None
        if path is not None:
            self._origin_map[path] = module

        # Collect all hooks by module origin and name
        hooks = []
        if path is not None and path in self._hook_map:
            hooks.extend(self._hook_map[path])
        if module.__name__ in self._hook_map:
            hooks.extend(self._hook_map[module.__name__])

        if hooks:
            log.debug("Calling %d registered hooks on import of module '%s'", len(hooks), module.__name__)
            for hook in hooks:
                hook(module)

    @classmethod
    def get_by_origin(cls, _origin: Path) -> t.Optional[ModuleType]:
        """Lookup a module by its origin."""
        cls._check_installed()

        instance = t.cast(ModuleWatchdog, cls._instance)

        resolved_path = _resolve(_origin)
        if resolved_path is not None:
            path = str(resolved_path)
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

    @classmethod
    def register_origin_hook(cls, origin: Path, hook: ModuleHookType) -> None:
        """Register a hook to be called when the module with the given origin is
        imported.

        The hook will be called with the module object as argument.
        """
        cls._check_installed()

        # DEV: Under the hypothesis that this is only ever called by the probe
        # poller thread, there are no further actions to take. Should this ever
        # change, then thread-safety might become a concern.
        resolved_path = _resolve(origin)
        if resolved_path is None:
            raise ValueError("Cannot resolve module origin %s" % origin)

        path = str(resolved_path)

        log.debug("Registering hook '%r' on path '%s'", hook, path)
        instance = t.cast(ModuleWatchdog, cls._instance)
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
    def unregister_origin_hook(cls, origin: Path, hook: ModuleHookType) -> None:
        """Unregister the hook registered with the given module origin and
        argument.
        """
        cls._check_installed()

        resolved_path = _resolve(origin)
        if resolved_path is None:
            raise ValueError("Module origin %s cannot be resolved", origin)

        path = str(resolved_path)

        instance = t.cast(ModuleWatchdog, cls._instance)
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
    def register_module_hook(cls, module: str, hook: ModuleHookType) -> None:
        """Register a hook to be called when the module with the given name is
        imported.

        The hook will be called with the module object as argument.
        """
        cls._check_installed()

        log.debug("Registering hook '%r' on module '%s'", hook, module)
        instance = t.cast(ModuleWatchdog, cls._instance)
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
    def unregister_module_hook(cls, module: str, hook: ModuleHookType) -> None:
        """Unregister the hook registered with the given module name and
        argument.
        """
        cls._check_installed()

        instance = t.cast(ModuleWatchdog, cls._instance)
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
    def after_module_imported(cls, module: str) -> t.Callable[[ModuleHookType], None]:
        def _(hook: ModuleHookType) -> None:
            cls.register_module_hook(module, hook)

        return _

    @classmethod
    def register_pre_exec_module_hook(
        cls: t.Type["ModuleWatchdog"], cond: PreExecHookCond, hook: PreExecHookType
    ) -> None:
        """Register a hook to execute before/instead of exec_module.

        The pre exec_module hook is executed before the module is executed
        to allow for changed modules to be executed as needed. To ensure
        that the hook is applied only to the modules that are required,
        the condition is evaluated against the module name.
        """
        cls._check_installed()

        log.debug("Registering pre_exec module hook '%r' on condition '%s'", hook, cond)
        instance = t.cast(ModuleWatchdog, cls._instance)
        instance._pre_exec_module_hooks.append((cond, hook))
