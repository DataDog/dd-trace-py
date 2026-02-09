from typing import Dict

from wrapt import wrap_function_wrapper as _w


try:
    import azure.durable_functions as durable_functions
except Exception:
    durable_functions = None

from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.azure_functions.utils import patched_get_functions
from ddtrace.contrib.internal.trace_utils import unwrap as _u


def get_version() -> str:
    try:
        from importlib.metadata import version

        return version("azure-functions-durable")
    except Exception:
        return getattr(durable_functions, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"azure.durable_functions": ">=1.2.1"}


def patch():
    """
    Patch `azure.durable_functions` module for tracing.
    """
    if durable_functions is None:
        return
    if getattr(durable_functions, "_datadog_patch", False):
        return
    durable_functions._datadog_patch = True
    _patch_dfapp()


def _patch_dfapp():
    try:
        from azure.durable_functions.decorators import durable_app
    except Exception:
        return

    if not hasattr(durable_app, "DFApp"):
        return

    Pin().onto(durable_app.DFApp)
    _w("azure.durable_functions", "DFApp.get_functions", patched_get_functions)


def unpatch():
    if durable_functions is None:
        return
    if not getattr(durable_functions, "_datadog_patch", False):
        return
    durable_functions._datadog_patch = False

    try:
        from azure.durable_functions.decorators import durable_app
    except Exception:
        durable_app = None
    if durable_app is not None and hasattr(durable_app, "DFApp"):
        _u(durable_app.DFApp, "get_functions")
