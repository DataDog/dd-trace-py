from typing import Dict
import webbrowser

from wrapt import wrap_function_wrapper as _w

from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.settings.asm import config as asm_config


def get_version():
    # type: () -> str
    return ""


def _supported_versions() -> Dict[str, str]:
    return {"webbrowser": "*"}


def patch():
    """patch the built-in webbrowser methods for tracing"""
    if getattr(webbrowser, "__datadog_patch", False):
        return
    webbrowser.__datadog_patch = True

    if asm_config._load_modules:
        from ddtrace.appsec._common_module_patches import wrapped_request_D8CB81E472AF98A2 as _wrap_open

        _w("webbrowser", "open", _wrap_open)

    if asm_config._iast_enabled:
        from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
        from ddtrace.appsec._iast.constants import VULN_SSRF

        _set_metric_iast_instrumented_sink(VULN_SSRF)


def unpatch():
    """unpatch any previously patched modules"""
    if not getattr(webbrowser, "__datadog_patch", False):
        return
    webbrowser.__datadog_patch = False

    _u(webbrowser, "open")
