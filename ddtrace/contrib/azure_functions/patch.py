import azure.functions
from azure.functions import __version__

from ddtrace import config
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap


config._add(
    "azure_functions",
    {
        "distributed_tracing": True,
        "_default_service": schematize_service_name("azure_functions"),
    },
)


def get_version():
    return str(__version__)


def patch():
    # Check to see if we have patched azure.functions yet or not
    if getattr(azure.functions, "_datadog_patch", False):
        return
    azure.functions._datadog_patch = True

    # find function and wrap it
    wrap(azure.functions.decorators.function_app.Function.__init__, _wrapped_init)
    pass


def unpatch():
    # Undo the monkey patching that patch() did here
    if getattr(azure.functions, "_datadog_patch", False):
        unwrap(azure.functions.decorators.function_app.Function.__init__, _wrapped_init)
        azure.functions._datadog_patch = False


def _wrapped_init(func, args, kwargs):
    return func(*args, **kwargs)
