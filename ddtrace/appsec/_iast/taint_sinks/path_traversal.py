from typing import Any

from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_sink
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


def check_and_report_path_traversal(*args: Any, **kwargs: Any) -> None:
    if asm_config.is_iast_request_enabled and PathTraversal.has_quota():
        try:
            increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, PathTraversal.vulnerability_type)
            _set_metric_iast_executed_sink(PathTraversal.vulnerability_type)
            filename_arg = args[0] if args else kwargs.get("file", None)
            if is_pyobject_tainted(filename_arg):
                PathTraversal.report(evidence_value=filename_arg)
        except Exception:  # nosec
            # FIXME: see below
            # log.debug("Unexpected exception while reporting vulnerability", exc_info=True)
            pass
    # FIXME: this is commented out because logs can execute _open which can runs the check_and_report_path_traversal
    # entering an infinite loop
    # else:
    #     log.debug("IAST: no vulnerability quota to analyze more sink points")
