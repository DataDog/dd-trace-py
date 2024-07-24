import webbrowser

from ddtrace.appsec._common_module_patches import wrapped_request_D8CB81E472AF98A2 as _wrap_open
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast.constants import VULN_SSRF
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.settings.asm import config as asm_config
from ddtrace.vendor.debtcollector import deprecate
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ..trace_utils import unwrap as _u


def _get_version():
    # type: () -> str
    return ""


def get_version():
    deprecate(
        "get_version is deprecated",
        message="get_version is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _get_version()


def patch():
    """patch the built-in webbrowser methods for tracing"""
    if getattr(webbrowser, "__datadog_patch", False):
        return
    webbrowser.__datadog_patch = True

    _w("webbrowser", "open", _wrap_open)

    if asm_config._iast_enabled:
        _set_metric_iast_instrumented_sink(VULN_SSRF)


def unpatch():
    """unpatch any previously patched modules"""
    if not getattr(webbrowser, "__datadog_patch", False):
        return
    webbrowser.__datadog_patch = False

    _u(webbrowser, "open")
