import sys
import typing as t

from ddtrace.settings._core import DDConfig
from ddtrace.settings._telemetry import report_telemetry as _report_telemetry


def parse_modules(value: t.Union[str, None]) -> t.List[str]:
    if not isinstance(value, str):
        return []

    fragments = [s.strip() for s in value.split(",")]
    return [f for f in fragments if f != ""]


class ErrorTrackingConfig(DDConfig):
    __prefix__ = "dd.error.tracking.report.handled.errors"

    _report_handled_errors = DDConfig.v(str, "enabled", default="")
    # Specify the modules (user and third party mixed) for which we report handled exceptions
    _modules_to_report = DDConfig.v(list, "enabled.modules", parser=parse_modules, default=[])

    # Report only handled exceptions if an unhandled exception occurs in the span
    # Experimental feature: will likely be removed
    _report_after_unhandled = DDConfig.v(bool, "after_unhandled", default=False)

    """
    At the moment, we are also logging the exceptions so ET can fingerprint the exceptions
    It will be removed when Error Track is GA
    """
    _internal_logger = DDConfig.var(str, "logger", default="")

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
    _instrument_user_code = False
    _instrument_third_party_code = False
    _instrument_all = False
    enabled = False


config = ErrorTrackingConfig()
if (not config._modules_to_report) is False or config._report_handled_errors in ["all", "user", "third_party"]:
    config._configured_modules = config._modules_to_report
    if config._report_handled_errors == "user":
        config._instrument_user_code = True
    elif config._report_handled_errors == "third_party":
        config._instrument_third_party_code = True
    elif config._report_handled_errors == "all":
        config._instrument_all = True
    config.enabled = True
    _report_telemetry(config)
