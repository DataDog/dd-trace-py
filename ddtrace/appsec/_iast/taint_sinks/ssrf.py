from typing import Callable

from ddtrace.internal.logger import get_logger

from ..._constants import IAST_SPAN_TAGS
from .. import oce
from .._metrics import increment_iast_span_metric
from ..constants import VULN_SSRF
from ..processor import AppSecIastSpanProcessor
from ._base import VulnerabilityBase


log = get_logger(__name__)


@oce.register
class SSRF(VulnerabilityBase):
    vulnerability_type = VULN_SSRF


def _iast_report_ssrf(func: Callable, *args, **kwargs):
    report_ssrf = kwargs.get("url", False)
    if report_ssrf:
        from .._metrics import _set_metric_iast_executed_sink

        _set_metric_iast_executed_sink(SSRF.vulnerability_type)
        increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, SSRF.vulnerability_type)
        if AppSecIastSpanProcessor.is_span_analyzed() and SSRF.has_quota():
            try:
                from .._taint_tracking import is_pyobject_tainted

                if is_pyobject_tainted(report_ssrf):
                    SSRF.report(evidence_value=report_ssrf)
            except Exception:
                log.debug("Unexpected exception while reporting vulnerability", exc_info=True)
