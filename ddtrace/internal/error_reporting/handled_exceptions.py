import sys
import types
from types import ModuleType

from ddtrace.settings.error_reporting import _er_config
from ddtrace.internal.module import BaseModuleWatchdog

inject_handled_exception_reporting = None

# Import are noqa'd otherwise some formatters will helpfully remove them
if sys.version_info >= (3, 10):  # and sys.version_info < (3, 12):
    from ddtrace.internal.error_reporting.handled_exceptions_by_bytecode import _inject_handled_exception_reporting

    inject_handled_exception_reporting = _inject_handled_exception_reporting

CONFIGURED_MODULES: list[str] = _er_config.reported_handled_exceptions
INSTRUMENTABLE_TYPES = (types.FunctionType, types.MethodType, type)


class HandledExceptionReportingWatchdog(BaseModuleWatchdog):

    _instrumented_modules: set[str] = set()

    def after_import(self, module: ModuleType):
        if not module.__name__:
            return

        self._instrument_conditionally(module.__name__)

    def after_install(self):
        # There might be modules that are already loaded at the time of installation, so we need to instrument them
        # if they have been configured.
        for module_name in sys.modules.keys():
            self._instrument_conditionally(module_name)

    def _instrument_conditionally(self, module_name: str):
        for enabled_module in CONFIGURED_MODULES:
            if module_name.startswith(enabled_module):
                self._instrument_module(module_name)
                break

    def _instrument_module(self, module_name: str):
        if inject_handled_exception_reporting is None:
            return

        if module_name in self._instrumented_modules:
            return

        mod = sys.modules[module_name]
        names = dir(mod)

        for name in names:
            obj = mod.__dict__[name]
            if type(obj) in INSTRUMENTABLE_TYPES and obj.__module__ == module_name and not name.startswith("__"):
                self._instrument_obj(obj)

    def _instrument_obj(self, obj):
        if inject_handled_exception_reporting is None:
            return

        if type(obj) in (types.FunctionType, types.MethodType):
            # functions/methods
            inject_handled_exception_reporting(obj)  # type: ignore
        elif type(obj) is type:
            # classes
            for candidate in dir(obj):
                if type(obj) in (types.FunctionType, types.MethodType, type):
                    self._instrument_obj(candidate)
