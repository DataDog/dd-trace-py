import inspect

from ddtrace.appsec._iast._metrics import _set_iast_error_metric
from ddtrace.appsec._iast._utils import _is_iast_debug_enabled
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def iast_taint_log_error(msg):
    if _is_iast_debug_enabled():
        stack = inspect.stack()
        frame_info = "\n".join("%s %s" % (frame_info.filename, frame_info.lineno) for frame_info in stack[:7])
        log.debug("[IAST] Propagation error. %s:\n%s", msg, frame_info)
    _set_iast_error_metric("[IAST] Propagation error. %s" % msg)
