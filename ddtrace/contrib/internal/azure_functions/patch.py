import os

import azure.functions as azure_functions
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.ext import SpanKind
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.formats import asbool
from ddtrace.trace import Pin

from .utils import create_context
from .utils import wrap_function_with_tracing


config._add(
    "azure_functions",
    dict(
        _default_service=schematize_service_name("azure_functions"),
        distributed_tracing=asbool(os.getenv("DD_AZURE_FUNCTIONS_DISTRIBUTED_TRACING", default=True)),
    ),
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
    _w("azure.functions", "FunctionApp.get_functions", _patched_get_functions)


def _patched_get_functions(wrapped, instance, args, kwargs):
    functions = wrapped(*args, **kwargs)

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    for function in functions:
        trigger = function.get_trigger()

        if not trigger:
            continue

        trigger_type = trigger.get_binding_name()

        bindings = function.get_bindings()
        input_binding = next((binding for binding in bindings if binding.direction == 0), None)  # IN
        if not input_binding:
            continue

        function_name = function.get_function_name()
        func = function.get_user_function()

        if trigger_type == "httpTrigger":
            function._func = _patched_http_trigger(pin, func, function_name, input_binding)
        elif trigger_type == "timerTrigger":
            function._func = _patched_timer_trigger(pin, func, function_name)
        elif trigger_type == "serviceBusTrigger":
            function._func = _patched_service_bus_trigger(pin, func, function_name)

    return functions


def _patched_http_trigger(pin, func, function_name, input_binding):
    trigger = "Http"
    trigger_arg_name = input_binding.name

    def context_factory(kwargs):
        req = kwargs.get(trigger_arg_name)
        return create_context("azure.functions.patched_route_request", pin, headers=req.headers)

    def pre_dispatch(ctx, kwargs):
        req = kwargs.get(trigger_arg_name)
        ctx.set_item("req_span", ctx.span)
        return ("azure.functions.request_call_modifier", (ctx, config.azure_functions, req))

    def post_dispatch(ctx, res):
        return ("azure.functions.start_response", (ctx, config.azure_functions, res, function_name, trigger))

    return wrap_function_with_tracing(func, context_factory, pre_dispatch=pre_dispatch, post_dispatch=post_dispatch)


def _patched_service_bus_trigger(pin, func, function_name):
    trigger = "ServiceBus"

    def context_factory(kwargs):
        resource_name = f"{trigger} {function_name}"
        return create_context("azure.functions.patched_service_bus", pin, resource_name)

    def pre_dispatch(ctx, kwargs):
        ctx.set_item("trigger_span", ctx.span)
        return (
            "azure.functions.trigger_call_modifier",
            (ctx, config.azure_functions, function_name, trigger, SpanKind.CONSUMER),
        )

    return wrap_function_with_tracing(func, context_factory, pre_dispatch=pre_dispatch)


def _patched_timer_trigger(pin, func, function_name):
    trigger = "Timer"

    def context_factory(kwargs):
        resource_name = f"{trigger} {function_name}"
        return create_context("azure.functions.patched_timer", pin, resource_name)

    def pre_dispatch(ctx, kwargs):
        ctx.set_item("trigger_span", ctx.span)
        return (
            "azure.functions.trigger_call_modifier",
            (ctx, config.azure_functions, function_name, trigger, SpanKind.INTERNAL),
        )

    return wrap_function_with_tracing(func, context_factory, pre_dispatch=pre_dispatch)


def unpatch():
    if not getattr(azure_functions, "_datadog_patch", False):
        return
    azure_functions._datadog_patch = False

    _u(azure_functions.FunctionApp, "get_functions")
