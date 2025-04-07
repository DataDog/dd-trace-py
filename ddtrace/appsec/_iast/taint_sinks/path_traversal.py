from typing import Any

from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._logs import iast_error
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_sink
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast._span_metrics import increment_iast_span_metric
from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast.constants import VULN_PATH_TRAVERSAL
from ddtrace.appsec._iast.taint_sinks._base import VulnerabilityBase
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)


@oce.register
class PathTraversal(VulnerabilityBase):
    vulnerability_type = VULN_PATH_TRAVERSAL
    secure_mark = VulnerabilityType.PATH_TRAVERSAL


IS_REPORTED_INTRUMENTED_SINK = False


def check_and_report_path_traversal(*args: Any, **kwargs: Any) -> None:
    global IS_REPORTED_INTRUMENTED_SINK
    if not IS_REPORTED_INTRUMENTED_SINK:
        _set_metric_iast_instrumented_sink(VULN_PATH_TRAVERSAL)
        IS_REPORTED_INTRUMENTED_SINK = True
    try:
        if asm_config.is_iast_request_enabled:
            filename_arg = args[0] if args else kwargs.get("file", None)
            if PathTraversal.has_quota() and PathTraversal.is_tainted_pyobject(filename_arg):
                PathTraversal.report(evidence_value=filename_arg)

            # Reports Span Metrics
            increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, PathTraversal.vulnerability_type)
            # Report Telemetry Metrics
            _set_metric_iast_executed_sink(PathTraversal.vulnerability_type)
    except Exception as e:
        iast_error(f"propagation::sink_point::Error in check_and_report_path_traversal. {e}")
