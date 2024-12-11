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

    reported_handled_exceptions = Env.var(
        list, "experimental_reported_handled_exceptions", parser=parse_modules, default=[])

    _internal_logger = Env.var(str, "experimental_reported_handled_exceptions_logger", default="")


_er_config = ErrorReportingConfig()
_report_telemetry(_er_config)
