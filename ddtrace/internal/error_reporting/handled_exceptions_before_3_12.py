from functools import lru_cache as cached
import sys
import types
from types import ModuleType

from ddtrace.internal.bytecode_injection.core import CallbackType
from ddtrace.internal.compat import Path
from ddtrace.internal.error_reporting.handled_exceptions_by_bytecode import _inject_handled_exception_reporting
from ddtrace.internal.error_reporting.hook import _default_datadog_exc_callback

# from ddtrace.internal.error_reporting.hook import _unhandled_exc_datadog_exc_callback
from ddtrace.internal.module import BaseModuleWatchdog
from ddtrace.internal.packages import _third_party_packages
from ddtrace.internal.packages import is_stdlib
from ddtrace.settings.error_reporting import _er_config


assert sys.version_info >= (3, 10) and sys.version_info < (3, 12)

INSTRUMENTABLE_TYPES = (types.FunctionType, types.MethodType, staticmethod, type)


def _install_bytecode_injection_reporting():
    InjectionHandledExceptionReportingWatchdog.install()


class HandledExceptionReportingInjector:
    _configured_modules: list[str] = list()
    _instrumented_modules: set[str] = set()
    _callback: CallbackType

    def __init__(self, configured_modules: list[str], callback: CallbackType | None = None):
        self._configured_modules = configured_modules
        self._callback = callback or _default_datadog_exc_callback

    def backfill(self):
        existing_modules = set(sys.modules.keys())
        for module_name in existing_modules:
            self.instrument_module_conditionally(module_name)

    def _is_user_code(self, module_name: str) -> bool:
        return self._has_file(module_name) and (
            not (self._is_std_lib(module_name) or self._is_third_party(module_name))
        )

    @cached(maxsize=256)
    def _has_file(self, module_name) -> bool:
        return hasattr(sys.modules[module_name], "__file__") and sys.modules[module_name].__file__ is not None

    @cached(maxsize=256)
    def _is_std_lib(self, module_name) -> bool:
        return module_name in sys.stdlib_module_names or (
            self._has_file(module_name) and is_stdlib(Path(sys.modules[module_name].__file__).resolve())  # type: ignore
        )

    @cached(maxsize=256)
    def _is_third_party(self, module_name: str) -> bool:
        return module_name.split(".")[0] in _third_party_packages()

    def instrument_module_conditionally(self, module_name: str):
        if _er_config._instrument_user_code and self._is_user_code(module_name):
            self._instrument_module(module_name)
        elif _er_config._instrument_third_party_code and self._is_third_party(module_name):
            self._instrument_module(module_name)
        if _er_config._instrument_python_internal_code and self._is_std_lib(module_name):
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
            obj = mod.__dict__[name]
            if type(obj) in INSTRUMENTABLE_TYPES and obj.__module__ == module_name and not name.startswith("__"):
                self._instrument_obj(obj)

    def _instrument_obj(self, obj):
        if type(obj) in (types.FunctionType, types.MethodType, staticmethod) and not self._is_reserved(obj.__name__):
            # functions/methods
            _inject_handled_exception_reporting(obj, callback=self._callback)
        elif type(obj) is type:
            # classes
            for candidate in obj.__dict__.keys():
                if type(obj) in INSTRUMENTABLE_TYPES and not self._is_reserved(candidate):
                    self._instrument_obj(obj.__dict__[candidate])

    def _is_reserved(self, name: str) -> bool:
        return name.startswith("__") and name != "__call__"


class InjectionHandledExceptionReportingWatchdog(BaseModuleWatchdog):
    _injector: HandledExceptionReportingInjector

    def after_import(self, module: ModuleType):
        self._injector.instrument_module_conditionally(module.__name__)

    def after_install(self):
        self._injector = HandledExceptionReportingInjector(_er_config._configured_modules)

        # There might be modules that are already loaded at the time of installation, so we need to instrument them
        # if they have been configured.
        existing_modules = set(sys.modules.keys())
        for module_name in existing_modules:
            self._injector.instrument_module_conditionally(module_name)
