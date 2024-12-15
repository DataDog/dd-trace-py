import sys
import types
from types import ModuleType, CodeType
from .hook import _default_datadog_exc_callback

from ddtrace.settings.error_reporting import _er_config
from ddtrace.internal.module import BaseModuleWatchdog
if sys.version_info < (3, 12):
    from ddtrace.internal.error_reporting.handled_exceptions_by_bytecode import _inject_handled_exception_reporting

INSTRUMENTABLE_TYPES = (types.FunctionType, types.MethodType, type)


def init_handled_exceptions_reporting():
    if not _er_config.reported_handled_exceptions:
        return

    if sys.version_info >= (3, 12):
        _install_sys_monitoring_reporting()
    elif sys.version_info >= (3, 10):
        HandledExceptionReportingWatchdog.install()


def _install_sys_monitoring_reporting():
    assert sys.version_info >= (3, 12)
    _configured_modules: list[str] = _er_config.reported_handled_exceptions  # type:ignore

    def _exc_event_handler(code: CodeType, instruction_offset: int, exception: BaseException):
        _default_datadog_exc_callback(exc=exception)
        return True

    sys.monitoring.use_tool_id(sys.monitoring.DEBUGGER_ID, "datadog_handled_exceptions")
    sys.monitoring.register_callback(
        sys.monitoring.DEBUGGER_ID, sys.monitoring.events.EXCEPTION_HANDLED, _exc_event_handler
    )
    sys.monitoring.set_events(sys.monitoring.DEBUGGER_ID, sys.monitoring.events.EXCEPTION_HANDLED)


class HandledExceptionReportingWatchdog(BaseModuleWatchdog):
    _configured_modules: list[str] = list()
    _instrumented_modules: set[str] = set()

    def after_import(self, module: ModuleType):
        if not module.__name__:
            return

        self._instrument_conditionally(module.__name__)

    def after_install(self):
        self._configured_modules.extend(_er_config.reported_handled_exceptions)
        # There might be modules that are already loaded at the time of installation, so we need to instrument them
        # if they have been configured.
        existing_modules = set(sys.modules.keys())
        for module_name in existing_modules:
            self._instrument_conditionally(module_name)

    def _instrument_conditionally(self, module_name: str):
        for enabled_module in self._configured_modules:
            if module_name.startswith(enabled_module):
                self._instrument_module(module_name)
                break

    def _instrument_module(self, module_name: str):
        if module_name in self._instrumented_modules:
            return

        mod = sys.modules[module_name]
        names = dir(mod)

        for name in names:
            obj = mod.__dict__[name]
            if type(obj) in INSTRUMENTABLE_TYPES and obj.__module__ == module_name and not name.startswith("__"):
                self._instrument_obj(obj)

    def _instrument_obj(self, obj):
        if type(obj) in (types.FunctionType, types.MethodType):
            # functions/methods
            _inject_handled_exception_reporting(obj)  # type: ignore
        elif type(obj) is type:
            # classes
            for candidate in dir(obj):
                if type(obj) in (types.FunctionType, types.MethodType, type):
                    self._instrument_obj(candidate)
