from typing import Any

from ddtrace.internal.logger import get_logger

from ..._constants import IAST_SPAN_TAGS
from .. import oce
from .._metrics import increment_iast_span_metric
from ..constants import VULN_PATH_TRAVERSAL
from ..processor import AppSecIastSpanProcessor
from ._base import VulnerabilityBase


log = get_logger(__name__)


@oce.register
class PathTraversal(VulnerabilityBase):
    vulnerability_type = VULN_PATH_TRAVERSAL


def check_and_report_path_traversal(*args: Any, **kwargs: Any) -> None:
    if AppSecIastSpanProcessor.is_span_analyzed() and PathTraversal.has_quota():
        try:
            from .._metrics import _set_metric_iast_executed_sink
            from .._taint_tracking import is_pyobject_tainted

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
