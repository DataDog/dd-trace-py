from functools import lru_cache as cached
import sys
import types
from types import ModuleType

from ddtrace.internal.compat import Path
from ddtrace.internal.error_reporting.handled_exceptions_by_bytecode import _inject_handled_exception_reporting
from ddtrace.internal.error_reporting.hook import _default_datadog_exc_callback
from ddtrace.internal.error_reporting.hook import _unhandled_exc_datadog_exc_callback
from ddtrace.internal.injection.core import CallbackType
from ddtrace.internal.module import BaseModuleWatchdog
from ddtrace.internal.packages import is_third_party
from ddtrace.internal.packages import is_user_code
from ddtrace.settings.error_reporting import config


assert sys.version_info >= (3, 10) and sys.version_info < (3, 12)

INSTRUMENTABLE_TYPES = (types.FunctionType, types.MethodType, staticmethod, type)


def _install_bytecode_injection_reporting():
    InjectionHandledExceptionReportingWatchdog.install()


class HandledExceptionReportingInjector:
    _configured_modules: list[str] = list()
    _instrumented_modules: set[str] = set()
    _instrumented_obj: set[int] = set()
    _callback: CallbackType

    def __init__(self, configured_modules: list[str], callback: CallbackType | None = None):
        self._configured_modules = configured_modules
        self._callback = callback or _default_datadog_exc_callback

    def backfill(self):
        existing_modules = set(sys.modules.keys())
        for module_name in existing_modules:
            self.instrument_module_conditionally(module_name)

    @cached(maxsize=256)
    def _has_file(self, module) -> bool:
        return hasattr(module, "__file__") and module.__file__ is not None

    def instrument_module_conditionally(self, module_name: str):
        module = sys.modules[module_name]
        if self._has_file(module) is False:
            return
        module_path = Path(module.__file__).resolve()  # type: ignore
        if config._instrument_user_code and is_user_code(module_path):
            self._instrument_module(module_name)
        elif config._instrument_third_party_code and is_third_party(module_path):
            self._instrument_module(module_name)
        else:
            for enabled_module in self._configured_modules:
                if module_name.startswith(enabled_module):
                    self._instrument_module(module_name)
                    break

    def _instrument_module(self, module_name: str):
        if module_name in self._instrumented_modules:
            return
        self._instrumented_modules.add(module_name)

        mod = sys.modules[module_name]
        names = dir(mod)

        for name in names:
            if name in mod.__dict__:
                obj = mod.__dict__[name]
                if (
                    type(obj) in INSTRUMENTABLE_TYPES
                    and (module_name == "__main__" or obj.__module__ == module_name)
                    and not name.startswith("__")
                ):
                    self._instrument_obj(obj)

    def _instrument_obj(self, obj):
        self._instrumented_obj.add(hash(obj))
        if (
            type(obj) in (types.FunctionType, types.MethodType, staticmethod)
            and hasattr(obj, "__name__")
            and not self._is_reserved(obj.__name__)
        ):
            _inject_handled_exception_reporting(obj, callback=self._callback)
        elif type(obj) is type:
            # classes
            for candidate in obj.__dict__.keys():
                if (
                    type(obj.__dict__[candidate]) in INSTRUMENTABLE_TYPES
                    and not self._is_reserved(candidate)
                    and hash(obj.__dict__[candidate]) not in self._instrumented_obj
                ):
                    self._instrument_obj(obj.__dict__[candidate])

    def _is_reserved(self, name: str) -> bool:
        return name.startswith("__") and name != "__call__"


_injector: HandledExceptionReportingInjector | None = None


def instrument_main() -> None:
    if _injector is not None:
        _injector.instrument_module_conditionally("__main__")


class InjectionHandledExceptionReportingWatchdog(BaseModuleWatchdog):
    def after_import(self, module: ModuleType):
        _injector.instrument_module_conditionally(module.__name__)  # type: ignore

    def after_install(self):
        global _injector
        if config._report_after_unhandled is False:
            _injector = HandledExceptionReportingInjector(config._configured_modules)
        else:
            _injector = HandledExceptionReportingInjector(
                config._configured_modules, _unhandled_exc_datadog_exc_callback
            )

        # There might be modules that are already loaded at the time of installation, so we need to instrument them
        # if they have been configured.
        existing_modules = set(sys.modules.keys())
        existing_modules.remove("__main__")
        for module_name in existing_modules:
            _injector.instrument_module_conditionally(module_name)
