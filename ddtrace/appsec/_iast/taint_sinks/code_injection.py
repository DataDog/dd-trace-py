import inspect
from typing import Text

from ddtrace.appsec._common_module_patches import try_unwrap
from ddtrace.appsec._constants import IAST
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._logs import iast_error
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_sink
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast._patch import set_and_check_module_is_patched
from ddtrace.appsec._iast._patch import set_module_unpatched
from ddtrace.appsec._iast._patch import try_wrap_function_wrapper
from ddtrace.appsec._iast._span_metrics import increment_iast_span_metric
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast.constants import VULN_CODE_INJECTION
from ddtrace.appsec._iast.taint_sinks._base import VulnerabilityBase
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)


def get_version() -> Text:
    return ""


def patch():
    if not asm_config._iast_enabled:
        return

    if not set_and_check_module_is_patched("builtins", default_attr="_datadog_code_injection_patch"):
        return

    try_wrap_function_wrapper("builtins", "eval", _iast_coi)
    # TODO: wrap exec functions is very dangerous because it needs and modifies locals and globals from the original
    #  function
    # try_wrap_function_wrapper("builtins", "exec", _iast_coi_exec)

    _set_metric_iast_instrumented_sink(VULN_CODE_INJECTION)


def unpatch():
    try_unwrap("builtins", "eval")
    # try_unwrap("builtins", "exec")

    set_module_unpatched("builtins", default_attr="_datadog_code_injection_patch")


def _iast_coi(wrapped, instance, args, kwargs):
    if asm_config._iast_enabled and len(args) >= 1:
        _iast_report_code_injection(args[0])

    caller_frame = None
    if len(args) > 1:
        func_globals = args[1]
    elif kwargs.get("globals"):
        func_globals = kwargs.get("globals")
    else:
        frames = inspect.currentframe()
        caller_frame = frames.f_back
        func_globals = caller_frame.f_globals

    if len(args) > 2:
        func_locals = args[2]
    elif kwargs.get("locals"):
        func_locals = kwargs.get("locals")
    else:
        if caller_frame is None:
            frames = inspect.currentframe()
            caller_frame = frames.f_back
        func_locals = caller_frame.f_locals

    return wrapped(args[0], func_globals, func_locals)


@oce.register
class CodeInjection(VulnerabilityBase):
    vulnerability_type = VULN_CODE_INJECTION


def _iast_report_code_injection(code_string: Text):
    reported = False
    try:
        if asm_config.is_iast_request_enabled:
            if isinstance(code_string, IAST.TEXT_TYPES) and CodeInjection.has_quota():
                if is_pyobject_tainted(code_string):
                    CodeInjection.report(evidence_value=code_string)
            increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, CodeInjection.vulnerability_type)
            _set_metric_iast_executed_sink(CodeInjection.vulnerability_type)
    except Exception as e:
        iast_error(f"propagation::sink_point::Error in _iast_report_code_injection. {e}")
    return reported
