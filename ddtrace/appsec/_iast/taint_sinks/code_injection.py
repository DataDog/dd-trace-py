import inspect
from typing import Text

from ddtrace.appsec._common_module_patches import try_unwrap
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._iast_request_context import is_iast_request_enabled
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_sink
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast._metrics import increment_iast_span_metric
from ddtrace.appsec._iast._patch import set_and_check_module_is_patched
from ddtrace.appsec._iast._patch import set_module_unpatched
from ddtrace.appsec._iast._patch import try_wrap_function_wrapper
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast.constants import VULN_CODE_INJECTION
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config

from ._base import VulnerabilityBase


log = get_logger(__name__)


def get_version() -> Text:
    return ""


def patch():
    if not asm_config._iast_enabled:
        return

    if not set_and_check_module_is_patched("ast", default_attr="_datadog_code_injection_patch"):
        return

    try_wrap_function_wrapper("builtins", "eval", _iast_coi)
    # TODO: wrap exec functions is very dangerous because it needs and modifies locals and globals from the original
    #  function
    # try_wrap_function_wrapper("builtins", "exec", _iast_coi_exec)
    try_wrap_function_wrapper("ast", "literal_eval", _iast_coi)

    _set_metric_iast_instrumented_sink(VULN_CODE_INJECTION)


def unpatch():
    try_unwrap("builtins", "eval")
    try_unwrap("builtins", "exec")
    try_unwrap("ast", "literal_eval")

    set_module_unpatched("flask", default_attr="_datadog_code_injection_patch")


def _iast_coi(wrapped, instance, args, kwargs):
    if asm_config._iast_enabled and len(args) >= 1:
        _iast_report_code_injection(args[0])

    return wrapped(*args, **kwargs)


def _iast_coi_exec(wrapped, instance, args, kwargs):
    if asm_config._iast_enabled and len(args) >= 1:
        _iast_report_code_injection(args[0])

    caller_frame = inspect.currentframe().f_back.f_back
    if caller_frame is None:
        return wrapped(*args, **kwargs)

    caller_globals = caller_frame.f_globals
    caller_locals = caller_frame.f_locals

    original_globals = {}
    if len(args) > 1:
        original_globals = args[1]

    original_locals = {}
    if len(args) > 2:
        original_locals = args[2]

    return wrapped(args[0], {**caller_globals, **original_globals}, {**caller_locals, **original_locals})


@oce.register
class CodeInjection(VulnerabilityBase):
    vulnerability_type = VULN_CODE_INJECTION


def _iast_report_code_injection(code_string: Text):
    increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, CodeInjection.vulnerability_type)
    _set_metric_iast_executed_sink(CodeInjection.vulnerability_type)

    if is_iast_request_enabled() and CodeInjection.has_quota():
        if is_pyobject_tainted(code_string):
            CodeInjection.report(evidence_value=code_string)
