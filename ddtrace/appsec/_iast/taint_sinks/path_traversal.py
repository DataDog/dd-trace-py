from typing import Any

from ddtrace.internal.logger import get_logger

from ..._constants import IAST_SPAN_TAGS
from .. import oce
from .._metrics import increment_iast_span_metric
from ..constants import EVIDENCE_PATH_TRAVERSAL
from ..constants import VULN_PATH_TRAVERSAL
from ..processor import AppSecIastSpanProcessor
from ._base import VulnerabilityBase


log = get_logger(__name__)


@oce.register
class PathTraversal(VulnerabilityBase):
    vulnerability_type = VULN_PATH_TRAVERSAL
    evidence_type = EVIDENCE_PATH_TRAVERSAL

    @classmethod
    def report(cls, evidence_value=None, sources=None):
        if isinstance(evidence_value, (str, bytes, bytearray)):
            from .._taint_tracking import taint_ranges_as_evidence_info

            evidence_value, sources = taint_ranges_as_evidence_info(evidence_value)
        super(PathTraversal, cls).report(evidence_value=evidence_value, sources=sources)


def get_version():
    # type: () -> str
    return ""


def check_and_report_path_traversal(*args: Any, **kwargs: Any) -> None:
    if AppSecIastSpanProcessor.is_span_analyzed() and PathTraversal.has_quota():
        try:
            from .._metrics import _set_metric_iast_executed_sink
            from .._taint_tracking import is_pyobject_tainted

            increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, PathTraversal.vulnerability_type)
            _set_metric_iast_executed_sink(PathTraversal.vulnerability_type)
            if is_pyobject_tainted(args[0]):
                PathTraversal.report(evidence_value=args[0])
        except Exception:
            log.debug("Unexpected exception while reporting vulnerability", exc_info=True)
    else:
        log.debug("IAST: no vulnerability quota to analyze more sink points")
