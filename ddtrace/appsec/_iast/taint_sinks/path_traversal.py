from typing import Any

from ddtrace.internal.logger import get_logger

from ..._constants import IAST_SPAN_TAGS
from .. import oce
from .._metrics import _set_metric_iast_instrumented_sink
from .._metrics import increment_iast_span_metric
from .._patch import set_and_check_module_is_patched
from .._patch import set_module_unpatched
from ..constants import VULN_PATH_TRAVERSAL
from ..processor import AppSecIastSpanProcessor
from ._base import VulnerabilityBase


log = get_logger(__name__)


@oce.register
class PathTraversal(VulnerabilityBase):
    vulnerability_type = VULN_PATH_TRAVERSAL


def get_version():
    # type: () -> str
    return ""


def unpatch_iast():
    # type: () -> None
    set_module_unpatched("builtins", default_attr="_datadog_path_traversal_patch")


def patch():
    # type: () -> None
    """Wrap functions which interact with file system."""
    if not set_and_check_module_is_patched("builtins", default_attr="_datadog_path_traversal_patch"):
        return
    _set_metric_iast_instrumented_sink(VULN_PATH_TRAVERSAL)


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


def open_path_traversal(*args, **kwargs):
    check_and_report_path_traversal(*args, **kwargs)
    return open(*args, **kwargs)
