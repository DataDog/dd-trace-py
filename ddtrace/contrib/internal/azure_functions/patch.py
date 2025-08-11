import os
from typing import Dict

import azure.functions as azure_functions
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.ext import SpanKind
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.formats import asbool
from ddtrace.trace import Pin

from .utils import create_context
from .utils import message_list_has_single_context
from .utils import wrap_function_with_tracing


config._add(
    "azure_functions",
    dict(
        _default_service=schematize_service_name("azure_functions"),
        distributed_tracing=asbool(os.getenv("DD_AZURE_FUNCTIONS_DISTRIBUTED_TRACING", default=True)),
    ),
)


def get_version() -> str:
    return getattr(azure_functions, "__version__", "")


def _supported_versions() -> Dict[str, str]:
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
    _w("azure.functions", "FunctionApp.get_functions", _patched_get_functions)


def _patched_get_functions(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    functions = wrapped(*args, **kwargs)

    for function in functions:
        trigger = function.get_trigger()
        if not trigger:
            continue

        trigger_type = trigger.get_binding_name()
        trigger_details = trigger.get_dict_repr()
        trigger_arg_name = trigger.name

        function_name = function.get_function_name()
        func = function.get_user_function()

        if trigger_type == "httpTrigger":
            function._func = _wrap_http_trigger(pin, func, function_name, trigger_arg_name)
        elif trigger_type == "timerTrigger":
            function._func = _wrap_timer_trigger(pin, func, function_name)
        elif trigger_type == "serviceBusTrigger":
            function._func = _wrap_service_bus_trigger(pin, func, function_name, trigger_arg_name, trigger_details)

    return functions


def _wrap_http_trigger(pin, func, function_name, trigger_arg_name):
    trigger_type = "Http"

    def context_factory(kwargs):
        req = kwargs.get(trigger_arg_name)
        return create_context("azure.functions.patched_route_request", pin, headers=req.headers)

    def pre_dispatch(ctx, kwargs):
        req = kwargs.get(trigger_arg_name)
        return ("azure.functions.request_call_modifier", (ctx, config.azure_functions, req))

    def post_dispatch(ctx, res):
        return ("azure.functions.start_response", (ctx, config.azure_functions, res, function_name, trigger_type))

    return wrap_function_with_tracing(func, context_factory, pre_dispatch=pre_dispatch, post_dispatch=post_dispatch)


def _wrap_service_bus_trigger(pin, func, function_name, trigger_arg_name, trigger_details):
    trigger_type = "ServiceBus"

    def context_factory(kwargs):
        resource_name = f"{trigger_type} {function_name}"
        msg = kwargs.get(trigger_arg_name)

        # Reparent trace if single message or list of messages all with same context
        if isinstance(msg, azure_functions.ServiceBusMessage):
            application_properties = msg.application_properties
        elif (
            isinstance(msg, list)
            and msg
            and isinstance(msg[0], azure_functions.ServiceBusMessage)
            and message_list_has_single_context(msg)
        ):
            application_properties = msg[0].application_properties
        else:
            application_properties = None

        return create_context("azure.functions.patched_service_bus", pin, resource_name, headers=application_properties)

    def pre_dispatch(ctx, kwargs):
        msg = kwargs.get(trigger_arg_name)

        if isinstance(msg, azure_functions.ServiceBusMessage):
            message_id = msg.message_id
        else:
            message_id = None

        entity_name = trigger_details.get("topicName") or trigger_details.get("queueName")
        return (
            "azure.functions.service_bus_trigger_modifier",
            (ctx, config.azure_functions, function_name, trigger_type, SpanKind.CONSUMER, entity_name, message_id),
        )

    return wrap_function_with_tracing(func, context_factory, pre_dispatch=pre_dispatch)


def _wrap_timer_trigger(pin, func, function_name):
    trigger_type = "Timer"

    def context_factory(kwargs):
        resource_name = f"{trigger_type} {function_name}"
        return create_context("azure.functions.patched_timer", pin, resource_name)

    def pre_dispatch(ctx, kwargs):
        return (
            "azure.functions.trigger_call_modifier",
            (ctx, config.azure_functions, function_name, trigger_type, SpanKind.INTERNAL),
        )

    return wrap_function_with_tracing(func, context_factory, pre_dispatch=pre_dispatch)


def unpatch():
    if not getattr(azure_functions, "_datadog_patch", False):
        return
    azure_functions._datadog_patch = False

    _u(azure_functions.FunctionApp, "get_functions")
