import os
import sys
import traceback
from typing import Dict
from typing import Text

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._deduplications import deduplication
from ddtrace.internal import telemetry
from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL
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

_IAST_SPAN_METRICS: Dict[str, int] = {}


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
def _set_iast_error_metric(msg: Text) -> None:
    # Due to format_exc and format_exception returns the error and the last frame
    try:
        exception_type, exception_instance, _traceback_list = sys.exc_info()
        res = []
        # first 10 frames are this function, the exception in aspects and the error line
        res.extend(traceback.format_stack(limit=10))

        # get the frame with the error and the error message
        result = traceback.format_exception(exception_type, exception_instance, _traceback_list)
        res.extend(result[1:])

        stack_trace = "".join(res)
        tags = {
            "lib_language": "python",
        }
        telemetry.telemetry_writer.add_log(TELEMETRY_LOG_LEVEL.ERROR, msg, stack_trace=stack_trace, tags=tags)
    except Exception:
        log.warning("Error reporting ASM WAF logs metrics", exc_info=True)


@metric_verbosity(TELEMETRY_MANDATORY_VERBOSITY)
def _set_metric_iast_instrumented_source(source_type):
    from ._taint_tracking._native.taint_tracking import origin_to_str  # noqa: F401

    telemetry.telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE_TAG_IAST, "instrumented.source", 1, (("source_type", origin_to_str(source_type)),)
    )


@metric_verbosity(TELEMETRY_MANDATORY_VERBOSITY)
def _set_metric_iast_instrumented_propagation():
    telemetry.telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_IAST, "instrumented.propagation", 1)


@metric_verbosity(TELEMETRY_MANDATORY_VERBOSITY)
def _set_metric_iast_instrumented_sink(vulnerability_type, counter=1):
    telemetry.telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE_TAG_IAST, "instrumented.sink", counter, (("vulnerability_type", vulnerability_type),)
    )


@metric_verbosity(TELEMETRY_INFORMATION_VERBOSITY)
def _set_metric_iast_executed_source(source_type):
    from ._taint_tracking._native.taint_tracking import origin_to_str  # noqa: F401

    telemetry.telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE_TAG_IAST, "executed.source", 1, (("source_type", origin_to_str(source_type)),)
    )


@metric_verbosity(TELEMETRY_INFORMATION_VERBOSITY)
def _set_metric_iast_executed_sink(vulnerability_type):
    telemetry.telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE_TAG_IAST, "executed.sink", 1, (("vulnerability_type", vulnerability_type),)
    )


def _request_tainted():
    from ._taint_tracking import num_objects_tainted

    return num_objects_tainted()


@metric_verbosity(TELEMETRY_INFORMATION_VERBOSITY)
def _set_metric_iast_request_tainted():
    total_objects_tainted = _request_tainted()
    if total_objects_tainted > 0:
        telemetry.telemetry_writer.add_count_metric(
            TELEMETRY_NAMESPACE_TAG_IAST, "request.tainted", total_objects_tainted
        )


def _set_span_tag_iast_request_tainted(span):
    total_objects_tainted = _request_tainted()

    if total_objects_tainted > 0:
        span.set_tag(IAST_SPAN_TAGS.TELEMETRY_REQUEST_TAINTED, total_objects_tainted)


def _set_span_tag_iast_executed_sink(span):
    data = get_iast_span_metrics()

    if data is not None:
        for key, value in data.items():
            if key.startswith(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK):
                span.set_tag(key, value)

    reset_iast_span_metrics()


def increment_iast_span_metric(prefix: str, metric_key: str, counter: int = 1) -> None:
    data = get_iast_span_metrics()
    full_key = prefix + "." + metric_key.lower()
    result = data.get(full_key, 0)
    data[full_key] = result + counter


def get_iast_span_metrics() -> Dict:
    return _IAST_SPAN_METRICS


def reset_iast_span_metrics() -> None:
    _IAST_SPAN_METRICS.clear()
