from typing import Any

from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_sink
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast._metrics import increment_iast_span_metric
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast.constants import VULN_PATH_TRAVERSAL
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config

from ._base import VulnerabilityBase


log = get_logger(__name__)


@oce.register
class PathTraversal(VulnerabilityBase):
    vulnerability_type = VULN_PATH_TRAVERSAL


IS_REPORTED_INTRUMENTED_SINK = False


def check_and_report_path_traversal(*args: Any, **kwargs: Any) -> None:
    global IS_REPORTED_INTRUMENTED_SINK
    if not IS_REPORTED_INTRUMENTED_SINK:
        _set_metric_iast_instrumented_sink(VULN_PATH_TRAVERSAL)
        IS_REPORTED_INTRUMENTED_SINK = True

    if asm_config.is_iast_request_enabled and PathTraversal.has_quota():
        filename_arg = args[0] if args else kwargs.get("file", None)
        if is_pyobject_tainted(filename_arg):
            PathTraversal.report(evidence_value=filename_arg)
    increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, PathTraversal.vulnerability_type)
    _set_metric_iast_executed_sink(PathTraversal.vulnerability_type)
