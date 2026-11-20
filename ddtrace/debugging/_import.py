from pathlib import Path
import sys
from types import ModuleType

from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleHookType
from ddtrace.internal.module import ModuleWatchdog


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

        # Build the origin map now rather than on first use. While a source is
        # imported again, import hooks rely on it still referring to the module
        # previously imported from it, which a map first built from sys.modules
        # at that point would have already lost.
        self._om = self._origin_map

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

            super().register_origin_hook(origin, hook)

    @classmethod
    def unregister_origin_hook(cls, origin: Path, hook: ModuleHookType) -> None:
        with cls._lock:
            try:
                cls._locations.remove(str(origin))
            except KeyError:
                # Nothing to unregister.
                return

            return super().unregister_origin_hook(origin, hook)

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
