import os

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._deduplications import deduplication
from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE_TAG_IAST


log = get_logger(__name__)

TELEMETRY_OFF_NAME = "OFF"
TELEMETRY_DEBUG_NAME = "DEBUG"
TELEMETRY_MANDATORY_NAME = "MANDATORY"
TELEMETRY_INFORMATION_NAME = "INFORMATION"

TELEMETRY_DEBUG_VERBOSITY = 10
TELEMETRY_INFORMATION_VERBOSITY = 20
TELEMETRY_MANDATORY_VERBOSITY = 30
TELEMETRY_OFF_VERBOSITY = 40

METRICS_REPORT_LVLS = (
    (TELEMETRY_DEBUG_VERBOSITY, TELEMETRY_DEBUG_NAME),
    (TELEMETRY_INFORMATION_VERBOSITY, TELEMETRY_INFORMATION_NAME),
    (TELEMETRY_MANDATORY_VERBOSITY, TELEMETRY_MANDATORY_NAME),
    (TELEMETRY_OFF_VERBOSITY, TELEMETRY_OFF_NAME),
)


def get_iast_metrics_report_lvl(*args, **kwargs):
    report_lvl_name = os.environ.get(IAST.TELEMETRY_REPORT_LVL, TELEMETRY_INFORMATION_NAME).upper()
    report_lvl = 3
    for lvl, lvl_name in METRICS_REPORT_LVLS:
        if report_lvl_name == lvl_name:
            return lvl
    return report_lvl


def metric_verbosity(lvl):
    def wrapper(f):
        if lvl >= get_iast_metrics_report_lvl():
            try:
                return f
            except Exception:
                log.warning("Error reporting IAST metrics", exc_info=True)
        return lambda: None  # noqa: E731

    return wrapper


@metric_verbosity(TELEMETRY_MANDATORY_VERBOSITY)
@deduplication
def _set_iast_error_metric(msg, stack_trace):
    # type: (str, str) -> None
    try:
        tags = {
            "lib_language": "python",
        }
        telemetry_writer.add_log("ERROR", msg, stack_trace=stack_trace, tags=tags)
    except Exception:
        log.warning("Error reporting ASM WAF logs metrics", exc_info=True)


@metric_verbosity(TELEMETRY_MANDATORY_VERBOSITY)
def _set_metric_iast_instrumented_source(source_type):
    from ._taint_tracking._native.taint_tracking import origin_to_str  # noqa: F401

    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE_TAG_IAST, "instrumented.source", 1, (("source_type", origin_to_str(source_type)),)
    )


@metric_verbosity(TELEMETRY_MANDATORY_VERBOSITY)
def _set_metric_iast_instrumented_propagation():
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_IAST, "instrumented.propagation", 1)


@metric_verbosity(TELEMETRY_MANDATORY_VERBOSITY)
def _set_metric_iast_instrumented_sink(vulnerability_type, counter=1):
    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE_TAG_IAST, "instrumented.sink", counter, (("vulnerability_type", vulnerability_type),)
    )


@metric_verbosity(TELEMETRY_INFORMATION_VERBOSITY)
def _set_metric_iast_executed_source(source_type):
    from ._taint_tracking._native.taint_tracking import origin_to_str  # noqa: F401

    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE_TAG_IAST, "executed.source", 1, (("source_type", origin_to_str(source_type)),)
    )


@metric_verbosity(TELEMETRY_INFORMATION_VERBOSITY)
def _set_metric_iast_executed_sink(vulnerability_type):
    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE_TAG_IAST, "executed.sink", 1, (("vulnerability_type", vulnerability_type),)
    )


@metric_verbosity(TELEMETRY_INFORMATION_VERBOSITY)
def _set_metric_iast_request_tainted():
    from ._taint_tracking import num_objects_tainted

    total_objects_tainted = num_objects_tainted()
    if total_objects_tainted > 0:
        telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_IAST, "request.tainted", total_objects_tainted)
