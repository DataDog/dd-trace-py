import functools

import azure.functions as azure_functions
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_cloud_faas_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.trace import Pin


config._add(
    "azure_functions",
    {
        "_default_service": schematize_service_name("azure_functions"),
    },
)


def get_version():
    # type: () -> str
    return getattr(azure_functions, "__version__", "")


def patch():
    """
    Patch `azure.functions` module for tracing
    """
    # Check to see if we have patched azure.functions yet or not
    if getattr(azure_functions, "_datadog_patch", False):
        return
    azure_functions._datadog_patch = True

    Pin().onto(azure_functions.FunctionApp)
    _w("azure.functions", "FunctionApp.function_name", _patched_function_name)
    _w("azure.functions", "FunctionApp.route", _patched_route)
    _w("azure.functions", "FunctionApp.timer_trigger", _patched_timer_trigger)


def _patched_function_name(wrapped, instance, args, kwargs):
    Pin.override(instance, tags={"function_name": kwargs.get("name")})
    return wrapped(*args, **kwargs)


def _patched_route(wrapped, instance, args, kwargs):
    trigger = "Http"
    trigger_arg_name = kwargs.get("trigger_arg_name", "req")

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    def _wrapper(func):
        if pin.tags and pin.tags.get("function_name"):
            function_name = pin.tags.get("function_name")
            Pin.override(instance, tags={"function_name": ""})
        else:
            function_name = func.__name__

        @functools.wraps(func)
        def wrap_function(*args, **kwargs):
            req = kwargs.get(trigger_arg_name)
            operation_name = schematize_cloud_faas_operation(
                "azure.functions.invoke", cloud_provider="azure", cloud_service="functions"
            )
            with core.context_with_data(
                "azure.functions.patched_route_request",
                span_name=operation_name,
                pin=pin,
                service=int_service(pin, config.azure_functions),
                span_type=SpanTypes.SERVERLESS,
            ) as ctx, ctx.span:
                ctx.set_item("req_span", ctx.span)
                core.dispatch("azure.functions.request_call_modifier", (ctx, config.azure_functions, req))
                res = None
                try:
                    res = func(*args, **kwargs)
                    return res
                finally:
                    core.dispatch(
                        "azure.functions.start_response", (ctx, config.azure_functions, res, function_name, trigger)
                    )

        return wrapped(*args, **kwargs)(wrap_function)

    return _wrapper


def _patched_timer_trigger(wrapped, instance, args, kwargs):
    trigger = "Timer"

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    def _wrapper(func):
        if pin.tags and pin.tags.get("function_name"):
            function_name = pin.tags.get("function_name")
            Pin.override(instance, tags={"function_name": ""})
        else:
            function_name = func.__name__

        @functools.wraps(func)
        def wrap_function(*args, **kwargs):
            operation_name = schematize_cloud_faas_operation(
                "azure.functions.invoke", cloud_provider="azure", cloud_service="functions"
            )
            with core.context_with_data(
                "azure.functions.patched_timer",
                span_name=operation_name,
                pin=pin,
                resource=function_name,
                service=int_service(pin, config.azure_functions),
                span_type=SpanTypes.SERVERLESS,
            ) as ctx, ctx.span:
                ctx.set_item("timer_span", ctx.span)
                core.dispatch(
                    "azure.functions.timer_call_modifier",
                    (ctx, config.azure_functions, function_name, trigger),
                )
                func(*args, **kwargs)

        return wrapped(*args, **kwargs)(wrap_function)

    return _wrapper


def unpatch():
    if not getattr(azure_functions, "_datadog_patch", False):
        return
    azure_functions._datadog_patch = False

    _u(azure_functions.FunctionApp, "route")
