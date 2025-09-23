from typing import Union

from ddtrace.appsec._iast._metrics import _set_iast_error_metric
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)


def iast_instrumentation_ast_patching_debug_log(msg, *args, **kwargs):
    log.debug("iast::instrumentation::ast_patching::%s", msg, *args, **kwargs)


def iast_ast_debug_log(msg, *args, **kwargs):
    iast_instrumentation_ast_patching_debug_log(f"ast::{msg}", *args, **kwargs)


def iast_compiling_debug_log(msg, *args, **kwargs):
    if asm_config._iast_debug:
        iast_instrumentation_ast_patching_debug_log(f"compiling::{msg}", *args, **kwargs)


def iast_instrumentation_wrapt_debug_log(msg, *args, **kwargs):
    log.debug("iast::instrumentation::wrapt::%s", msg, *args, **kwargs)


def iast_propagation_listener_log_log(msg, *args, **kwargs):
    log.debug("iast::propagation::listener::%s", msg, *args, **kwargs)


def iast_propagation_debug_log(msg, *args, **kwargs):
    log.debug("iast::propagation::error::%s", msg, *args, **kwargs)


def iast_propagation_sink_point_debug_log(msg, *args, **kwargs):
    log.debug("iast::propagation::sink_point::%s", msg, *args, **kwargs)


def iast_instrumentation_ast_patching_errorr_log(msg):
    iast_error(msg, default_prefix="iast::instrumentation::ast_patching::")


def iast_propagation_error_log(msg, exc: Union[BaseException, tuple, None] = None):
    iast_error(msg, default_prefix="iast::propagation::error::", exc=exc)


def iast_error(msg, default_prefix="iast::", exc: Union[BaseException, tuple, None] = None):
    _set_iast_error_metric(f"{default_prefix}{msg}", exc=exc)
