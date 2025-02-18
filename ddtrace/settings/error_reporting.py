import sys
import typing as t

from envier import Env

from ddtrace.settings._core import report_telemetry as _report_telemetry


def parse_modules(value: t.Union[str, None]) -> t.List[str]:
    if not isinstance(value, str):
        return []

    fragments = [s.strip() for s in value.split(",")]
    return [f for f in fragments if f != ""]


class ErrorReportingConfig(Env):
    __prefix__ = "dd_trace"
    enable_handled_exceptions_reporting = Env.var(bool, "experimental_reported_handled_exceptions", default=False)
    _instrument_user_code = Env.var(bool, "experimental_reported_handled_exceptions_user", default=False)
    _instrument_third_party_code = Env.var(bool, "experimental_reported_handled_exceptions_third_party", default=False)
    _modules_to_report = Env.var(
        list, "experimental_reported_handled_exceptions_modules", parser=parse_modules, default=[]
    )
    _internal_logger = Env.var(str, "experimental_reported_handled_exceptions_logger", default="")
    _report_after_unhandled = Env.var(bool, "experimental_reported_handled_exceptions_after_unhandled", default=False)

    if sys.version_info >= (3, 12):
        """
        TOOL_ID must be in range 0 to 5 inclusive with
        sys.monitoring.DEBUGGER_ID = 0
        sys.monitoring.COVERAGE_ID = 1
        sys.monitoring.PROFILER_ID = 2
        sys.monitoring.OPTIMIZER_ID = 5

        We cannot use an existing one, otherwise for instance we cannot debug our program
        with the feature activate, therefore we take the first one available
        """
        HANDLED_EXCEPTIONS_MONITORING_ID = 3

    _configured_modules: t.List[str] = list()
    enabled = False


config = ErrorReportingConfig()
if (
    (not config._modules_to_report) is False
    or config.enable_handled_exceptions_reporting
    or config._instrument_user_code
    or config._instrument_third_party_code
):
    config._configured_modules = config._modules_to_report
    config.enabled = True
    _report_telemetry(config)
