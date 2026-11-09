from pathlib import Path
import sys
from types import ModuleType
import typing as t
from weakref import WeakValueDictionary as wvdict

from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleHookType
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.module import _resolve
from ddtrace.internal.module import origin


log = get_logger(__name__)


class DebuggerModuleWatchdog(ModuleWatchdog):
    _locations: set[str] = set()
    _import_hooks: list[ModuleHookType] = []
    # Serializes import processing with hook registrations from other threads
    # (e.g. new probes from remote configuration). Otherwise probes could be
    # installed on, or moved off, a module while its import hooks run, against
    # code that is being replaced, or before the origin map refers to the
    # replacement module. Reset after fork, in case another thread was holding
    # it at the time, so that imports in the child process cannot hang.
    _lock = forksafe.RLock()

    def __init__(self) -> None:
        super().__init__()

        # DEV: It would make more sense to make this a mapping of Path to ModuleType
        # but the WeakValueDictionary causes an ignored exception on shutdown
        # because the pathlib module is being garbage collected.
        self._om: t.Optional[t.MutableMapping[str, ModuleType]] = None

        # Build the origin map now rather than on first use. While a source is
        # imported again, import hooks rely on it still referring to the module
        # previously imported from it, which a map first built from sys.modules
        # at that point would have already lost.
        self._om = self._origin_map

    @property
    def _origin_map(self) -> t.MutableMapping[str, ModuleType]:
        def modules_with_origin(modules: t.Iterable[ModuleType]) -> t.MutableMapping[str, ModuleType]:
            result: t.MutableMapping[str, ModuleType] = wvdict()

            for m in modules:
                module_origin = origin(m)
                if module_origin is None:
                    continue

                try:
                    result[str(module_origin)] = m
                except TypeError:
                    # Some native extensions expose objects without weak-reference
                    # support. They cannot provide Python-level probe locations.
                    pass

            return result

        if self._om is None:
            try:
                self._om = modules_with_origin(sys.modules.values())
            except RuntimeError:
                # Another thread changed sys.modules. Use a snapshot for now
                # and retry the full mapping on the next access.
                return modules_with_origin(list(sys.modules.values()))
        return self._om

    @classmethod
    def get_by_origin(cls, _origin: Path) -> t.Optional[ModuleType]:
        """Lookup a module by its origin."""
        if not cls.is_installed():
            return None

        instance = t.cast(DebuggerModuleWatchdog, cls._instance)

        resolved_path = _resolve(_origin)
        if resolved_path is not None:
            path = str(resolved_path)
            module = instance._origin_map.get(path)
            if module is not None:
                return module

            # Check if this is the __main__ module
            main_module = sys.modules.get("__main__")
            if main_module is not None and origin(main_module) == resolved_path:
                # Register for future lookups
                instance._origin_map[path] = main_module

                return main_module

        return None

    def after_import(self, module: ModuleType) -> None:
        # Hooked into the actual execution of the module, rather than its
        # compilation, since code can be compiled without being executed (e.g.
        # with an explicit call to the loader's get_code).
        with self._lock:
            for hook in self._import_hooks:
                try:
                    hook(module)
                except Exception:
                    log.exception("Failed to call import hook on module %s", module.__name__)

            module_path = origin(module)
            path = str(module_path) if module_path is not None else None
            if path is not None:
                self._origin_map[path] = module

            hooks = list(self._hook_map.get(path, ())) if path is not None else []
            if hooks:
                log.debug("Calling %d registered hooks on import of module '%s'", len(hooks), module.__name__)
                for hook in hooks:
                    hook(module)

            super().after_import(module)

    @classmethod
    def register_import_hook(cls, hook: ModuleHookType) -> None:
        """Register a hook to be called whenever a module has been executed.

        This includes re-executions of an already loaded module (e.g.
        importlib.reload()). The hook runs before any origin or module hook
        registered for the module.
        """
        if hook not in cls._import_hooks:
            cls._import_hooks.append(hook)

    @classmethod
    def unregister_import_hook(cls, hook: ModuleHookType) -> None:
        try:
            cls._import_hooks.remove(hook)
        except ValueError:
            pass

    @classmethod
    def uninstall(cls) -> None:
        super().uninstall()
        # The hooks tracked by these locations were registered with the
        # instance that was just discarded. Keeping them would make a later
        # instance skip registering hooks for the same locations.
        cls._locations.clear()

    @classmethod
    def register_origin_hook(cls, origin: Path, hook: ModuleHookType) -> None:
        with cls._lock:
            if str(origin) in cls._locations:
                # We already have a hook for this origin, don't register a new one
                # but invoke it directly instead, if the module was already loaded.
                module = cls.get_by_origin(origin)
                if module is not None:
                    hook(module)

                return

            cls._locations.add(str(origin))

            cls.install()

            resolved_path = _resolve(origin)
            if resolved_path is None:
                log.warning("Cannot resolve module origin %s", origin)
                return

            path = str(resolved_path)

            log.debug("Registering hook '%r' on path '%s'", hook, path)
            instance = t.cast(DebuggerModuleWatchdog, cls._instance)
            instance._hook_map[path].append(hook)
            try:
                module = instance.get_by_origin(resolved_path)
                if module is None:
                    # The path was resolved but we still haven't seen any module
                    # that has it as origin. Nothing more we can do for now.
                    return
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
        with cls._lock:
            try:
                cls._locations.remove(str(origin))
            except KeyError:
                # Nothing to unregister.
                return

            if not cls.is_installed():
                return

            resolved_path = _resolve(origin)
            if resolved_path is None:
                log.warning("Module origin %s cannot be resolved", origin)
                return

            path = str(resolved_path)

            instance = t.cast(DebuggerModuleWatchdog, cls._instance)
            if path not in instance._hook_map:
                log.warning("No hooks registered for origin %s", origin)
                return

            try:
                if path in instance._hook_map:
                    hooks = instance._hook_map[path]
                    hooks.remove(hook)
                    if not hooks:
                        del instance._hook_map[path]
            except ValueError:
                log.warning("Hook %r not registered for origin %s", hook, origin)
                return

    @classmethod
    def register_module_hook(cls, module: str, hook: ModuleHookType) -> None:
        with cls._lock:
            if module in cls._locations:
                # We already have a hook for this origin, don't register a new one
                # but invoke it directly instead, if the module was already loaded.
                mod = sys.modules.get(module)
                if mod is not None:
                    hook(mod)

                return

            cls._locations.add(module)

            super().register_module_hook(module, hook)

    @classmethod
    def unregister_module_hook(cls, module: str, hook: ModuleHookType) -> None:
        with cls._lock:
            try:
                cls._locations.remove(module)
            except KeyError:
                # Nothing to unregister.
                return

            return super().unregister_module_hook(module, hook)

    @classmethod
    def on_run_module(cls, module: ModuleType) -> None:
        if cls._instance is not None:
            # Treat run module as an import to trigger import hooks and register
            # the module's origin.
            cls._instance.after_import(module)
