import typing as t

from ddtrace.internal.settings._core import DDConfig


def parse_modules(value: t.Union[str, None]) -> list[str]:
    if not isinstance(value, str):
        return []

    fragments = [s.strip() for s in value.split(",")]
    return [f for f in fragments if f != ""]


class ErrorTrackingConfig(DDConfig):
    __prefix__ = "dd.error.tracking"

    _report_handled_errors = DDConfig.v(str, "handled.errors", default="")
    # Specify the modules (user and third party mixed) for which we report handled exceptions
    _modules_to_report = DDConfig.v(list, "handled.errors.include", parser=parse_modules, default=[])

    # Tool name used with the central monitoring_registry for sys.monitoring slot allocation.
    HANDLED_EXCEPTIONS_TOOL_NAME = "datadog_handled_exceptions"

    _configured_modules: list[str] = list()
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
