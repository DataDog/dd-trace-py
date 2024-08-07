from ddtrace import config
from ddtrace import tracer
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap


config._add(
    "azure",
    {
        "distributed_tracing": True,
        "_default_service": schematize_service_name("azure"),
    },
)


def get_version():
    from azure import __version__
    return str(__version__)


def patch():
    import azure.functions
    from azure.functions.decorators.function_app import Function
    print("starting patch")
    # Check to see if we have patched azure.functions yet or not
    if getattr(azure.functions, "_datadog_patch", False):
        print("checking if patched")
        return
    azure.functions._datadog_patch = True
    print("not patched so wrapping")

    # find function and wrap it
    wrap(Function.get_user_function, _traced_user_function)


def unpatch():
    import azure.functions
    from azure.functions.decorators.function_app import Function
    print("in unpatch")
    # Undo the monkey patching that patch() did here
    if getattr(azure.functions, "_datadog_patch", False):
        print("un-patching")
        unwrap(Function.get_user_function, _traced_user_function)
        azure.functions._datadog_patch = False


def _traced_user_function(get_user_function, args, kwargs):
    # create spec for span name and tag values that follows aws_lambda ex: resource name, service, name tags, etc...
    # find the convention
    # identify semantics for span name, resources name, operation name and service name

    # resrouce name = function name
    # service name = should be azure.functions as default
    # span name =
    def wrapped_user_func():
        print("in wrapper user func")
        with tracer.trace("top-level-span"):
            print("starting span context")
            return get_user_function()

    return wrapped_user_func
