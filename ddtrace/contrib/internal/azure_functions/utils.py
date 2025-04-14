import functools

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_cloud_faas_operation
from ddtrace.trace import Pin


def create_context(context_name, pin, resource=None):
    operation_name = schematize_cloud_faas_operation(
        "azure.functions.invoke", cloud_provider="azure", cloud_service="functions"
    )
    return core.context_with_data(
        context_name,
        span_name=operation_name,
        pin=pin,
        resource=resource,
        service=int_service(pin, config.azure_functions),
        span_type=SpanTypes.SERVERLESS,
    )


def get_function_name(pin, instance, func):
    if pin.tags and pin.tags.get("function_name"):
        function_name = pin.tags.get("function_name")
        Pin.override(instance, tags={"function_name": ""})
    else:
        function_name = func.__name__
    return function_name


def wrap_function(wrapped, instance, args, kwargs, trigger, context_name):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    def _wrapper(func):
        function_name = get_function_name(pin, instance, func)
        resource_name = f"{trigger} {function_name}"

        @functools.wraps(func)
        def wrap_function(*args, **kwargs):
            with create_context(context_name, pin, resource_name) as ctx, ctx.span:
                ctx.set_item("trigger_span", ctx.span)
                core.dispatch(
                    "azure.functions.trigger_call_modifier",
                    (ctx, config.azure_functions, function_name, trigger),
                )
                func(*args, **kwargs)

        return wrapped(*args, **kwargs)(wrap_function)

    return _wrapper
