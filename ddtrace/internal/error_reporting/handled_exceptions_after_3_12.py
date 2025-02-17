from functools import lru_cache as cached
import sys
from types import CodeType
from types import ModuleType
from typing import Callable

from ddtrace.internal.compat import Path
from ddtrace.internal.error_reporting.hook import _default_datadog_exc_callback
from ddtrace.internal.error_reporting.hook import _unhandled_exc_datadog_exc_callback
from ddtrace.internal.module import BaseModuleWatchdog
from ddtrace.internal.packages import is_third_party  # noqa: F401
from ddtrace.internal.packages import is_user_code  # noqa: F401
from ddtrace.settings.error_reporting import config


assert sys.version_info >= (3, 12)

INSTRUMENTED_FILE_PATHS = []


def create_should_report_exception_optimized(checks: set[str | None]) -> Callable[[str, Path], bool]:
    """
    Generate a precompiled version of `should_report_exception` that is as fast as static logic.

    :param checks: Set of checks to include (e.g., 'user', 'module', 'stdlib', 'thirdparty').
    :return: A callable function that evaluates the required checks.
    """
    # Start with the common frozen check
    conditions = []

    # Add conditions based on the requested checks
    if "all_user" in checks:
        conditions.append("is_user_code(file_path)")
    if "modules" in checks:
        conditions.append("file_name in INSTRUMENTED_FILE_PATHS")
    if "all_third_party" in checks:
        conditions.append("(is_third_party(file_path) and 'ddtrace' not in file_name)")

    # Combine all conditions into a single expression
    logic = "'frozen' not in file_name and (" + " or ".join(conditions) + ")"
    # Dynamically define the function using `exec`
    namespace = {}
    exec(f"def _should_report_exception(file_name: str, file_path: Path): return {logic}", globals(), namespace)
    return namespace["_should_report_exception"]


checks = {
    "all_user" if config._instrument_user_code else None,
    "all_third_party" if config._instrument_third_party_code else None,
    "modules" if (not config._configured_modules) is False else None,
} - {None}
_should_report_exception = create_should_report_exception_optimized(checks)


@cached(maxsize=2048)
def cached_should_report_exception(file_name: str):
    file_path = Path(file_name).resolve()
    return _should_report_exception(file_name, file_path)


def _install_sys_monitoring_reporting():
    MonitorHandledExceptionReportingWatchdog.install()

    def _exc_default_event_handler(code: CodeType, instruction_offset: int, exception: BaseException):
        if cached_should_report_exception(code.co_filename):
            _default_datadog_exc_callback(exc=exception)
        return True

    def _exc_after_unhandled_event_handler(code: CodeType, instruction_offset: int, exception: BaseException):
        if cached_should_report_exception(code.co_filename):
            _unhandled_exc_datadog_exc_callback(exc=exception)
        return True

    sys.monitoring.use_tool_id(config.HANDLED_EXCEPTIONS_MONITORING_ID, "datadog_handled_exceptions")
    if config._report_after_unhandled:
        sys.monitoring.register_callback(
            config.HANDLED_EXCEPTIONS_MONITORING_ID,
            sys.monitoring.events.EXCEPTION_HANDLED,
            _exc_after_unhandled_event_handler,
        )
    else:
        sys.monitoring.register_callback(
            config.HANDLED_EXCEPTIONS_MONITORING_ID,
            sys.monitoring.events.EXCEPTION_HANDLED,
            _exc_default_event_handler,
        )

    sys.monitoring.set_events(config.HANDLED_EXCEPTIONS_MONITORING_ID, sys.monitoring.events.EXCEPTION_HANDLED)


class MonitorHandledExceptionReportingWatchdog(BaseModuleWatchdog):
    _instrumented_modules: set[str] = set()
    _configured_modules: list[str] = config._configured_modules

    def conditionally_instrument_module(self, configured_modules: list[str], module_name: str, module: ModuleType):
        if len(configured_modules) == 0:
            return
        for enabled_module in configured_modules:
            if module_name.startswith(enabled_module):
                INSTRUMENTED_FILE_PATHS.append(module.__file__)
                break

    def after_import(self, module: ModuleType):
        if len(self._configured_modules) == 0:
            return

        module_name = module.__name__
        if module_name in self._instrumented_modules:
            return
        self._instrumented_modules.add(module_name)
        self.conditionally_instrument_module(self._configured_modules, module_name, module)

    def after_install(self):
        if len(self._configured_modules) == 0:
            return
        # There might be modules that are already loaded at the time of installation, so we need to instrument them
        # if they have been configured.
        existing_modules = set(sys.modules.keys())
        for module_name in existing_modules:
            self.conditionally_instrument_module(self._configured_modules, module_name, sys.modules[module_name])
