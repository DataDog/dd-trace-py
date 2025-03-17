import inspect

from ddtrace.appsec._iast._metrics import _set_iast_error_metric
from ddtrace.appsec._iast._utils import _is_iast_debug_enabled
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def iast_instrumentation_ast_patching_debug_log(msg, *args, **kwargs):
    log.debug("iast::instrumentation::ast_patching::%s", msg, *args, **kwargs)


def iast_ast_debug_log(msg, *args, **kwargs):
    iast_instrumentation_ast_patching_debug_log(f"ast::{msg}", *args, **kwargs)


def iast_compiling_debug_log(msg, *args, **kwargs):
    iast_instrumentation_ast_patching_debug_log(f"compiling::{msg}", *args, **kwargs)


def iast_instrumentation_wrapt_debug_log(msg, *args, **kwargs):
    log.debug("iast::instrumentation::wrapt::%s", msg, *args, **kwargs)


def iast_propagation_listener_log_log(msg, *args, **kwargs):
    log.debug("iast::propagation::listener::%s", msg, *args, **kwargs)


def iast_propagation_debug_log(msg, *args, **kwargs):
    log.debug("iast::propagation::error::%s", msg, *args, **kwargs)


def iast_propagation_error_log(msg):
    iast_error(msg, default_prefix="iast::propagation::error::")


def iast_error(msg, default_prefix="iast::"):
    if _is_iast_debug_enabled():
        stack = inspect.stack()
        frame_info = "\n".join("%s %s" % (frame_info.filename, frame_info.lineno) for frame_info in stack[:7])
        log.debug("%s. %s:\n%s", default_prefix, msg, frame_info)
    _set_iast_error_metric(f"{default_prefix}. {msg}")
