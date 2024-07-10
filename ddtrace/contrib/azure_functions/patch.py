import azure.functions
from azure.functions import __version__
from azure.functions.decorators.function_app import Function

from ddtrace import config
from ddtrace import tracer
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
    wrap(Function.get_user_function, _traced_user_function)


def unpatch():
    # Undo the monkey patching that patch() did here
    if getattr(azure.functions, "_datadog_patch", False):
        unwrap(Function.get_user_function, _traced_user_function)
        azure.functions._datadog_patch = False


def _traced_user_function(get_user_function, args, kwargs):
    # create spec for span name and tag values that follows aws_lambda ex: resource name, service, name tags, etc...
    # find the convention
    # identify semantics for span name, resources name, operation name and service name

    # resrouce name = function name
    # service name = should be azure.functions as default
    # span name =
    
    def wrapped_user_func(*wrapped_args, **wrapped_kwargs):
        with tracer.trace("top-level-span"):
            return get_user_function(*wrapped_args, **wrapped_kwargs)

    return wrapped_user_func
