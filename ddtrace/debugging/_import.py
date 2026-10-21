from pathlib import Path
import sys
from types import CodeType
from types import ModuleType

from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleHookType
from ddtrace.internal.module import ModuleWatchdog


log = get_logger(__name__)


class DebuggerModuleWatchdog(ModuleWatchdog):
    _locations: set[str] = set()
    _compile_hooks: list[ModuleHookType] = []

    def transform(self, code: CodeType, module: ModuleType) -> CodeType:
        for hook in self._compile_hooks:
            try:
                hook(module)
            except Exception:
                log.exception("Failed to call compile hook on module %s", module.__name__)

        return code

    @classmethod
    def register_compile_hook(cls, hook: ModuleHookType) -> None:
        """Register a hook to be called whenever a module is compiled.

        This includes recompilations of an already loaded module (e.g.
        importlib.reload()). The hook runs before the module code is executed,
        so it must not rely on the module's contents.
        """
        if hook not in cls._compile_hooks:
            cls._compile_hooks.append(hook)

    @classmethod
    def unregister_compile_hook(cls, hook: ModuleHookType) -> None:
        try:
            cls._compile_hooks.remove(hook)
        except ValueError:
            pass

    @classmethod
    def register_origin_hook(cls, origin: Path, hook: ModuleHookType) -> None:
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
        try:
            cls._locations.remove(str(origin))
        except KeyError:
            # Nothing to unregister.
            return

        return super().unregister_origin_hook(origin, hook)

    @classmethod
    def register_module_hook(cls, module: str, hook: ModuleHookType) -> None:
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
