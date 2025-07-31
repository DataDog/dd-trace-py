from typing import Text

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast._logs import iast_error
from ddtrace.appsec._iast._logs import iast_propagation_sink_point_debug_log
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_sink
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast._patch_modules import WrapFunctonsForIAST
from ddtrace.appsec._iast._span_metrics import increment_iast_span_metric
from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast.constants import VULN_CODE_INJECTION
from ddtrace.appsec._iast.taint_sinks._base import VulnerabilityBase
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)


def get_version() -> Text:
    return ""


_IS_PATCHED = False


def patch():
    global _IS_PATCHED
    if _IS_PATCHED and not asm_config._iast_is_testing:
        return

    if not asm_config._iast_enabled:
        return

    _IS_PATCHED = True

    iast_funcs = WrapFunctonsForIAST()

    iast_funcs.wrap_function("builtins", "eval", _iast_coi)

    # TODO: wrap exec functions is very dangerous because it needs and modifies locals and globals from the original
    #  function
    # iast_funcs.wrap_function("builtins", "exec", _iast_coi)
    iast_funcs.patch()

    _set_metric_iast_instrumented_sink(VULN_CODE_INJECTION)


class CodeInjection(VulnerabilityBase):
    vulnerability_type = VULN_CODE_INJECTION
    secure_mark = VulnerabilityType.CODE_INJECTION


def _iast_coi(wrapped, instance, args, kwargs):
    if len(args) >= 1:
        _iast_report_code_injection(args[0])
    try:
        # Import inspect locally to avoid gevent compatibility issues.
        # Top-level imports of inspect can interfere with gevent's monkey patching
        # and cause sporadic worker timeouts in Gunicorn applications.
        # See ddtrace/internal/iast/product.py for detailed explanation.
        import inspect

        caller_frame = None
        if len(args) > 1:
            func_globals = args[1]
        elif kwargs.get("globals"):
            func_globals = kwargs.get("globals")
        else:
            frames = inspect.currentframe()
            caller_frame = frames.f_back
            func_globals = caller_frame.f_globals

        func_locals_copy_to_check = None
        if len(args) > 2:
            func_locals = args[2]
        elif kwargs.get("locals"):
            func_locals = kwargs.get("locals")
        else:
            if caller_frame is None:
                frames = inspect.currentframe()
                caller_frame = frames.f_back
            func_locals = caller_frame.f_locals
            func_locals_copy_to_check = func_locals.copy() if func_locals else None
    except Exception as e:
        iast_propagation_sink_point_debug_log(f"Error in _iast_code_injection. {e}")
        return wrapped(*args, **kwargs)

    res = wrapped(args[0], func_globals, func_locals)

    # We need to perform this `func_locals_copy_to_check` check because of how Python handles `eval()` depending
    # on whether `locals` is provided. If we have code like `def evaluate(n): return n` and we call
    # `eval(code, my_globals)`, the new function will be stored in `my_globals["evaluate"]`. However, if we call
    # `eval(code, my_globals, my_locals)`, then the function will be stored in `my_locals["evaluate"]`. So, if `eval()`
    # is called without a `locals` argument, we need to transfer the newly created code from the local
    # context to the global one.
    try:
        if func_locals_copy_to_check is not None:
            diff_keys = set(func_locals) - set(func_locals_copy_to_check)
            for key in diff_keys:
                func_globals[key] = func_locals[key]
    except Exception as e:
        iast_propagation_sink_point_debug_log(f"Error in _iast_code_injection. {e}")

    return res


def _iast_report_code_injection(code_string: Text):
    reported = False
    try:
        if asm_config.is_iast_request_enabled:
            if code_string and isinstance(code_string, IAST.TEXT_TYPES) and CodeInjection.has_quota():
                if CodeInjection.is_tainted_pyobject(code_string):
                    CodeInjection.report(evidence_value=code_string)

            # Reports Span Metrics
            increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, CodeInjection.vulnerability_type)
            # Report Telemetry Metrics
            _set_metric_iast_executed_sink(CodeInjection.vulnerability_type)
    except Exception as e:
        iast_error(f"propagation::sink_point::Error in _iast_report_code_injection. {e}")
    return reported
