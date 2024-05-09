import webbrowser

from ddtrace.appsec._common_module_patches import wrapped_request_D8CB81E472AF98A2 as _wrap_open
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w
from ..trace_utils import unwrap as _u


def get_version():
    # type: () -> str
    return ""


def patch():
    """patch the built-in webbrowser methods for tracing"""
    if getattr(webbrowser, "__datadog_patch", False):
        return
    webbrowser.__datadog_patch = True

    _w("webbrowser", "BaseBrowser.open", _wrap_open)


def unpatch():
    """unpatch any previously patched modules"""
    if not getattr(webbrowser, "__datadog_patch", False):
        return
    webbrowser.__datadog_patch = False

    _u(webbrowser.BaseBrowser, "open")
