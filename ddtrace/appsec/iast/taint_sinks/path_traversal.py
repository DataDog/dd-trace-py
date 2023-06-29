from ddtrace.appsec.iast import oce
from ddtrace.appsec.iast._patch import set_and_check_module_is_patched
from ddtrace.appsec.iast._patch import set_module_unpatched
from ddtrace.appsec.iast._patch import try_wrap_function_wrapper
from ddtrace.appsec.iast.constants import EVIDENCE_PATH_TRAVERSAL
from ddtrace.appsec.iast.constants import VULN_PATH_TRAVERSAL
from ddtrace.appsec.iast.taint_sinks._base import VulnerabilityBase
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


@oce.register
class PathTraversal(VulnerabilityBase):
    vulnerability_type = VULN_PATH_TRAVERSAL
    evidence_type = EVIDENCE_PATH_TRAVERSAL


def unpatch_iast():
    # type: () -> None
    set_module_unpatched("builtins", default_attr="_datadog_path_traversal_patch")


def patch():
    # type: () -> None
    """Wrap functions which interact with file system."""
    if not set_and_check_module_is_patched("builtins", default_attr="_datadog_path_traversal_patch"):
        return

    try_wrap_function_wrapper("builtins", "open", wrapped_path_traversal)


@PathTraversal.wrap
def wrapped_path_traversal(wrapped, instance, args, kwargs):
    try:
        from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted

        if is_pyobject_tainted(args[0]):
            PathTraversal.report(evidence_value=args[0])
    except Exception:
        log.debug("Unexpected exception while reporting vulnerability", exc_info=True)

    return wrapped(*args, **kwargs)
