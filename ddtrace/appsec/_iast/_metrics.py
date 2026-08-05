from typing import Callable
from typing import Optional
from typing import TypeVar
from typing import Union

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._constants import TELEMETRY_INFORMATION_VERBOSITY
from ddtrace.appsec._constants import TELEMETRY_MANDATORY_VERBOSITY
from ddtrace.appsec._deduplications import deduplicate
from ddtrace.appsec._iast._iast_request_context_base import _num_objects_tainted_in_request
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import origin_to_str
from ddtrace.appsec._iast._utils import _is_iast_debug_enabled
from ddtrace.internal import telemetry
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


log = get_logger(__name__)

_IAST_SPAN_METRICS: dict[str, int] = {}
R = TypeVar("R")


def get_iast_metrics_report_lvl(*args: object, **kwargs: object) -> int:
    report_lvl_name = asm_config._iast_telemetry_report_lvl.upper()
    report_lvl = 3
    for lvl, lvl_name in IAST.METRICS_REPORT_LVLS:
        if report_lvl_name == lvl_name:
            return lvl
    return report_lvl


def metric_verbosity(lvl: int) -> Callable[[Callable[..., R]], Callable[..., Optional[R]]]:
    def wrapper(f: Callable[..., R]) -> Callable[..., Optional[R]]:
        if lvl >= get_iast_metrics_report_lvl():
            return f

        def disabled_metric(*args: object, **kwargs: object) -> None:
            return None

        return disabled_metric

    return wrapper


@metric_verbosity(TELEMETRY_MANDATORY_VERBOSITY)
@deduplicate
def _set_iast_error_metric(msg: str, exc: Union[BaseException, tuple[object, ...], None] = None) -> None:
    """This was originally implemented to analyze which services were triggering this issue, and we used that insight
    to refactor how IAST creates and destroys context. However, after that refactor, this information no longer
    provides value and only adds noise. So now, those telemetry metrics are only emitted if IAST is in debug mode
    """
    if _is_iast_debug_enabled():
        telemetry.telemetry_writer.add_error_log(msg, exc=exc)


@metric_verbosity(TELEMETRY_MANDATORY_VERBOSITY)
def _set_metric_iast_instrumented_source(source_type: OriginType) -> None:
    from ._taint_tracking import origin_to_str

    telemetry.telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE.IAST, "instrumented.source", 1, (("source_type", origin_to_str(source_type)),)
    )


@metric_verbosity(TELEMETRY_MANDATORY_VERBOSITY)
def _set_metric_iast_instrumented_propagation() -> None:
    telemetry.telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.IAST, "instrumented.propagation", 1)


@metric_verbosity(TELEMETRY_MANDATORY_VERBOSITY)
def _set_metric_iast_instrumented_sink(vulnerability_type: str, counter: int = 1) -> None:
    telemetry.telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE.IAST, "instrumented.sink", counter, (("vulnerability_type", vulnerability_type),)
    )


@metric_verbosity(TELEMETRY_INFORMATION_VERBOSITY)
def _set_metric_iast_executed_source(source_type: OriginType) -> None:
    from ._taint_tracking import origin_to_str

    telemetry.telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE.IAST, "executed.source", 1, (("source_type", origin_to_str(source_type)),)
    )


@metric_verbosity(TELEMETRY_INFORMATION_VERBOSITY)
def _set_metric_iast_executed_sink(vulnerability_type: str) -> None:
    telemetry.telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE.IAST, "executed.sink", 1, (("vulnerability_type", vulnerability_type),)
    )


@metric_verbosity(TELEMETRY_INFORMATION_VERBOSITY)
def _set_metric_iast_request_tainted() -> None:
    total_objects_tainted = _num_objects_tainted_in_request()
    if total_objects_tainted > 0:
        telemetry.telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.IAST, "request.tainted", total_objects_tainted)


def _metric_key_as_snake_case(key: Union[str, OriginType]) -> str:
    if isinstance(key, OriginType):
        key = origin_to_str(key)
    key = key.replace(".", "_")
    return key.lower()
