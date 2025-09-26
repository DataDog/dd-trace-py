from typing import Any
from typing import Callable

from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast._iast_request_context_base import is_iast_request_enabled
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_sink
from ddtrace.appsec._iast._span_metrics import increment_iast_span_metric
from ddtrace.appsec._iast.constants import DEFAULT_COMMAND_INJECTION_FUNCTIONS
from ddtrace.appsec._iast.constants import DEFAULT_PATH_TRAVERSAL_FUNCTIONS
from ddtrace.appsec._iast.constants import DEFAULT_SSRF_FUNCTIONS
from ddtrace.appsec._iast.constants import DEFAULT_WEAK_RANDOMNESS_FUNCTIONS
from ddtrace.appsec._iast.taint_sinks.command_injection import _iast_report_cmdi
from ddtrace.appsec._iast.taint_sinks.path_traversal import check_and_report_path_traversal
from ddtrace.appsec._iast.taint_sinks.ssrf import _iast_report_ssrf
from ddtrace.appsec._iast.taint_sinks.weak_randomness import WeakRandomness


# TODO: we also need a native version of this function!
def ast_function(
    func: Callable,
    flag_added_args: Any,
    *args: Any,
    **kwargs: Any,
) -> Any:
    instance = getattr(func, "__self__", None)
    func_name = getattr(func, "__name__", None)
    cls_name = ""
    if instance is not None and func_name:
        try:
            cls_name = instance.__class__.__name__
        except AttributeError:
            pass

    if flag_added_args > 0:
        args = args[flag_added_args:]

    # print(f"func! {func}")
    # if hasattr(func, "__module__"):
    #     print(f"func_name: {func_name}, module: {func.__module__}")
    #     print(DEFAULT_SSRF_FUNCTIONS.get(func.__module__))
    #     print(func_name in DEFAULT_SSRF_FUNCTIONS.get(func.__module__, ""))

    if (
        instance.__class__.__module__ == "random"
        and cls_name == "Random"
        and func_name in DEFAULT_WEAK_RANDOMNESS_FUNCTIONS
    ):
        if is_iast_request_enabled():
            if WeakRandomness.has_quota():
                WeakRandomness.report(evidence_value=cls_name + "." + func_name)

            # Reports Span Metrics
            increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, WeakRandomness.vulnerability_type)
            # Report Telemetry Metrics
            _set_metric_iast_executed_sink(WeakRandomness.vulnerability_type)

    elif (
        hasattr(func, "__module__")
        and DEFAULT_PATH_TRAVERSAL_FUNCTIONS.get(func.__module__)
        and func_name in DEFAULT_PATH_TRAVERSAL_FUNCTIONS[func.__module__]
    ):
        check_and_report_path_traversal(*args, **kwargs)
    elif (
        hasattr(func, "__module__")
        and DEFAULT_COMMAND_INJECTION_FUNCTIONS.get(func.__module__)
        and func_name in DEFAULT_COMMAND_INJECTION_FUNCTIONS[func.__module__]
    ):
        _iast_report_cmdi(func_name, *args, **kwargs)
    elif (
        hasattr(func, "__module__")
        and DEFAULT_SSRF_FUNCTIONS.get(func.__module__)
        and func_name in DEFAULT_SSRF_FUNCTIONS[func.__module__]
    ):
        _iast_report_ssrf(func_name, func.__module__, *args, **kwargs)
    return func(*args, **kwargs)
