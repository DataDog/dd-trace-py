import azure.functions as azure_functions
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings._config import _get_config
from ddtrace.internal.utils.formats import asbool

from .utils import patched_get_functions


config._add(
    "azure_functions",
    dict(
        _default_service=schematize_service_name("azure_functions"),
        distributed_tracing=asbool(_get_config("DD_AZURE_FUNCTIONS_DISTRIBUTED_TRACING", default=True)),
    ),
)


def get_version() -> str:
    return getattr(azure_functions, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"azure.functions": ">=1.10.1"}


def patch():
    """
    Patch `azure.functions` module for tracing
    """
    # Check to see if we have patched azure.functions yet or not
    if getattr(azure_functions, "_datadog_patch", False):
        return
    azure_functions._datadog_patch = True

    Pin().onto(azure_functions.FunctionApp)
    _w("azure.functions", "FunctionApp.get_functions", patched_get_functions)


def unpatch():
    if not getattr(azure_functions, "_datadog_patch", False):
        return
    azure_functions._datadog_patch = False

    _u(azure_functions.FunctionApp, "get_functions")
