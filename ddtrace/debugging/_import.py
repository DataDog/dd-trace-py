from pathlib import Path
import sys
from types import CodeType
from types import ModuleType
from typing import Set

from ddtrace.debugging._function.discovery import FunctionDiscovery
from ddtrace.internal.module import ModuleHookType
from ddtrace.internal.module import ModuleWatchdog


class DebuggerModuleWatchdog(ModuleWatchdog):
    _locations: Set[str] = set()

    def transform(self, code: CodeType, module: ModuleType) -> CodeType:
        return FunctionDiscovery.transformer(code, module)

    @classmethod
    def register_origin_hook(cls, origin: Path, hook: ModuleHookType) -> None:
        if origin in cls._locations:
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
