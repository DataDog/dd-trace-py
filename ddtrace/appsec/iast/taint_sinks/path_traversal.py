from ddtrace.internal.logger import get_logger

from .. import oce
from .._metrics import _set_metric_iast_instrumented_sink
from .._patch import set_and_check_module_is_patched
from .._patch import set_module_unpatched
from ..constants import EVIDENCE_PATH_TRAVERSAL
from ..constants import VULN_PATH_TRAVERSAL
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


def unpatch_iast():
    # type: () -> None
    set_module_unpatched("builtins", default_attr="_datadog_path_traversal_patch")


def patch():
    # type: () -> None
    """Wrap functions which interact with file system."""
    if not set_and_check_module_is_patched("builtins", default_attr="_datadog_path_traversal_patch"):
        return
    _set_metric_iast_instrumented_sink(VULN_PATH_TRAVERSAL)


def open_path_traversal(*args, **kwargs):
    if oce.request_has_quota and PathTraversal.has_quota():
        try:
            from .._taint_tracking import is_pyobject_tainted

            if is_pyobject_tainted(args[0]):
                PathTraversal.report(evidence_value=args[0])
        except Exception:
            log.debug("Unexpected exception while reporting vulnerability", exc_info=True)
    else:
        log.debug("IAST: no vulnerability quota to analyze more sink points")

    return open(*args, **kwargs)
