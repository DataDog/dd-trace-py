from ddtrace import config
from ddtrace.internal.schema import schematize_service_name
import azure.functions

from ...internal.utils.wrappers import unwrap
from ddtrace.vendor import wrapt
config._add(
    "azure.functions",
    {
        "distributed_tracing": True,
        "_default_service": schematize_service_name("azure.functions"),
    },
)


def get_version():
    return ""


def patch():
    # Check to see if we have patched azure.functions yet or not
    if getattr(azure.functions, "_datadog_patch", False):
        return
    azure.functions._datadog_patch = True

    # find function and wrap it
    wrapt.wrap_function_wrapper("azure.functions.decorators.function_app", "Function.__init__", _wrapped_init)
    pass


def unpatch():
    # Undo the monkey patching that patch() did here
    if getattr(azure.functions, "_datadog_patch", False):
        unwrap(azure.functions.decorators.function_app.Function, "__init__")
        azure.functions._datadog_patch = False


def _wrapped_init(func, args, kwargs):
    return func(*args, **kwargs)