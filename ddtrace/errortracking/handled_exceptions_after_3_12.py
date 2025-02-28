from functools import lru_cache as cached
import sys
from types import CodeType
from types import ModuleType
from typing import Callable

from ddtrace.errortracking.handled_exceptions_callbacks import _default_datadog_exc_callback
from ddtrace.errortracking.handled_exceptions_callbacks import _unhandled_exc_datadog_exc_callback
from ddtrace.internal.compat import Path
from ddtrace.internal.module import BaseModuleWatchdog
from ddtrace.internal.packages import filename_to_package  # noqa: F401
from ddtrace.internal.packages import is_stdlib  # noqa: F401
from ddtrace.internal.packages import is_third_party  # noqa: F401
from ddtrace.internal.packages import is_user_code  # noqa: F401
from ddtrace.settings.errortracking import config


INSTRUMENTED_FILE_PATHS = []


def create_should_report_exception_optimized(checks: set[str | None]) -> Callable[[str, Path], bool]:
    """
    sys.monitoring reports EVERY handled exceptions, including python internal ones.
    Therefore we need to filter based on the file_name/file_path. If this check is called
    many times (it is the case), it becomes costly.
    This function generates the version of `should_report_exception` that contains only the required checks
    """

    if len(checks) == 0:
        # It means that all exceptions should be reported. We still want to exclude python internals and ddtrace
        def should_report(file_name: str, file_path: Path) -> bool:
            return "frozen" not in file_name and is_stdlib(file_path) is False and "ddtrace" not in file_name

    elif "modules" in checks:
        # Specify the modules to instrument
        def should_report(file_name: str, file_path: Path) -> bool:
            return file_name in INSTRUMENTED_FILE_PATHS

    else:
        if "all_user" in checks:
            # User code
            def should_report(file_name: str, file_path: Path) -> bool:
                return "frozen" not in file_name and is_user_code(file_path)

        elif "all_third_party" in checks:
            # Third party package
            def should_report(file_name: str, file_path: Path) -> bool:
                return is_third_party(file_path) and filename_to_package(file_path).name != "ddtrace"

    return should_report


checks = {
    "all_user" if config._instrument_user_code else None,
    "all_third_party" if config._instrument_third_party_code else None,
    "modules" if (not config._configured_modules) is False else None,
} - {None}
_should_report_exception = create_should_report_exception_optimized(checks)


@cached(maxsize=4096)
def cached_should_report_exception(file_name: str):
    file_path = Path(file_name).resolve()
    return _should_report_exception(file_name, file_path)


def _install_sys_monitoring_reporting():
    if (not config._configured_modules) is False:
        MonitorHandledExceptionReportingWatchdog.install()

    sys.monitoring.use_tool_id(config.HANDLED_EXCEPTIONS_MONITORING_ID, "datadog_handled_exceptions")
    sys.monitoring.set_events(config.HANDLED_EXCEPTIONS_MONITORING_ID, sys.monitoring.events.EXCEPTION_HANDLED)

    if config._report_after_unhandled:
        # Report handled exception only if an unhandled exceptions occurred
        def _exc_after_unhandled_event_handler(code: CodeType, instruction_offset: int, exception: BaseException):
            if cached_should_report_exception(code.co_filename):
                _unhandled_exc_datadog_exc_callback(exc=exception)
            return True

        sys.monitoring.register_callback(
            config.HANDLED_EXCEPTIONS_MONITORING_ID,
            sys.monitoring.events.EXCEPTION_HANDLED,
            _exc_after_unhandled_event_handler,
        )

    else:
        # Report every handled exceptions
        def _exc_default_event_handler(code: CodeType, instruction_offset: int, exception: BaseException):
            if cached_should_report_exception(code.co_filename):
                _default_datadog_exc_callback(exc=exception)
            return True

        sys.monitoring.register_callback(
            config.HANDLED_EXCEPTIONS_MONITORING_ID,
            sys.monitoring.events.EXCEPTION_HANDLED,
            _exc_default_event_handler,
        )


class MonitorHandledExceptionReportingWatchdog(BaseModuleWatchdog):
    """
    This watchdog will be installed only if the MODULES env variables is enabled.

    Using sys.monitoring, we cannot iinstrument modules directly.
    This watchdog will add the path of the files we want to instrument to a list.
    The sys.monitoring callback will then check that the file of the error belongs to this list.
    """

    _instrumented_modules: set[str] = set()
    _configured_modules: list[str] = config._configured_modules

    # Instrumenting a module is adding its file path to INSTRUMENTED_FILE_PATHS
    def conditionally_instrument_module(self, configured_modules: list[str], module_name: str, module: ModuleType):
        for enabled_module in configured_modules:
            if module_name.startswith(enabled_module):
                INSTRUMENTED_FILE_PATHS.append(module.__file__)
                break

    def after_import(self, module: ModuleType):
        module_name = module.__name__
        if module_name in self._instrumented_modules:
            return
        self._instrumented_modules.add(module_name)
        self.conditionally_instrument_module(self._configured_modules, module_name, module)

    def after_install(self):
        # There might be modules that are already loaded at the time of installation, so we need to instrument them
        # if they have been configured.
        existing_modules = set(sys.modules.keys())
        for module_name in existing_modules:
            self.conditionally_instrument_module(self._configured_modules, module_name, sys.modules[module_name])
