from functools import lru_cache as cached
from pathlib import Path
import sys
from types import CodeType
from types import ModuleType
from typing import Callable

from ddtrace import tracer
from ddtrace.errortracking._handled_exceptions.callbacks import _default_errortracking_exc_callback
from ddtrace.internal import monitoring
from ddtrace.internal.module import BaseModuleWatchdog
from ddtrace.internal.packages import filename_to_package  # noqa: F401
from ddtrace.internal.packages import is_stdlib  # noqa: F401
from ddtrace.internal.packages import is_third_party  # noqa: F401
from ddtrace.internal.packages import is_user_code  # noqa: F401
from ddtrace.internal.settings.errortracking import config


INSTRUMENTED_FILE_PATHS: set[str] = set()


def create_should_report_exception_optimized(checks: set[str | None]) -> Callable[[str, Path], bool]:
    """Build the static filename classifier required by the enabled checks."""
    if "all_user" in checks:
        # User code
        def should_report(file_name: str, file_path: Path) -> bool:
            return "frozen" not in file_name and is_user_code(file_path)

    elif "all_third_party" in checks:
        # Third party package
        def should_report(file_name: str, file_path: Path) -> bool:
            # if the second part of the condition is eval it means filename_to_package does not return none
            return is_third_party(file_path) and filename_to_package(file_path).name != "ddtrace"  # type: ignore

    else:
        # It means that all exceptions should be reported. We still want to exclude python internals and ddtrace
        def should_report(file_name: str, file_path: Path) -> bool:
            return "frozen" not in file_name and is_stdlib(file_path) is False and "ddtrace" not in file_name

    return should_report


checks = {
    "all_user" if config._instrument_user_code else None,
    "all_third_party" if config._instrument_third_party_code else None,
    "modules" if (not config._configured_modules) is False else None,
} - {None}
_report_configured_modules = "modules" in checks
_static_checks = checks - {"modules"}
_should_report_exception = (
    create_should_report_exception_optimized(_static_checks)
    if _static_checks or not _report_configured_modules
    else None
)


@cached(maxsize=4096)
def _cached_should_report_exception(file_name: str) -> bool:
    """Cache only static path classification; configured module membership is dynamic."""
    assert _should_report_exception is not None  # nosec
    return _should_report_exception(file_name, Path(file_name).resolve())


def cached_should_report_exception(file_name: str) -> bool:
    if _report_configured_modules and file_name in INSTRUMENTED_FILE_PATHS:
        return True
    if _should_report_exception is None:
        return False
    return _cached_should_report_exception(file_name)


class _HandledExceptionHandler(monitoring.MonitoringEventHandler):
    """Report handled exceptions attached to an active span."""

    def on_exception_handled(self, code: CodeType, instruction_offset: int, exception: BaseException) -> None:
        span = tracer.current_span()
        if span and cached_should_report_exception(code.co_filename):
            _default_errortracking_exc_callback(span=span, exc=exception)


_handler = _HandledExceptionHandler()


def _install_sys_monitoring_reporting() -> None:
    if (not config._configured_modules) is False:
        MonitorHandledExceptionReportingWatchdog.install()
    monitoring.register_global(_handler)


def _uninstall_sys_monitoring_reporting() -> None:
    monitoring.unregister_global(_handler)


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
                if file_path := getattr(module, "__file__", None):
                    INSTRUMENTED_FILE_PATHS.add(file_path)
                break

    def __init__(self):
        super().__init__()
        # There might be modules that are already loaded at the time of installation, so we need to instrument them
        # if they have been configured.
        existing_modules = set(sys.modules.keys())
        for module_name in existing_modules:
            self.conditionally_instrument_module(self._configured_modules, module_name, sys.modules[module_name])

    def after_import(self, module: ModuleType):
        module_name = module.__name__
        if module_name in self._instrumented_modules:
            return
        self._instrumented_modules.add(module_name)
        self.conditionally_instrument_module(self._configured_modules, module_name, module)
