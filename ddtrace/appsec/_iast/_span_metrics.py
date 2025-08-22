from typing import Dict

from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast._iast_env import _get_iast_env
from ddtrace.appsec._iast._metrics import _metric_key_as_snake_case
from ddtrace.appsec._iast._utils import _num_objects_tainted_in_request


def _set_span_tag_iast_request_tainted(span):
    total_objects_tainted = _num_objects_tainted_in_request()

    if total_objects_tainted > 0:
        span.set_tag(IAST_SPAN_TAGS.TELEMETRY_REQUEST_TAINTED, total_objects_tainted)


def _set_span_tag_iast_executed_sink(span):
    data = get_iast_span_metrics()

    if data is not None:
        for key, value in data.items():
            if (
                key.startswith(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK)
                or key.startswith(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE)
                or key.startswith(IAST_SPAN_TAGS.TELEMETRY_SUPPRESSED_VULNERABILITY)
            ):
                span.set_tag(key, value)

    reset_iast_span_metrics()


def get_iast_span_metrics() -> Dict:
    env = _get_iast_env()
    return env.iast_span_metrics if env else dict()


def reset_iast_span_metrics() -> None:
    metrics = get_iast_span_metrics()
    metrics.clear()


def increment_iast_span_metric(prefix: str, metric_key: str, counter: int = 1) -> None:
    data = get_iast_span_metrics()
    full_key = prefix + "." + _metric_key_as_snake_case(metric_key)
    result = data.get(full_key, 0)
    data[full_key] = result + counter
