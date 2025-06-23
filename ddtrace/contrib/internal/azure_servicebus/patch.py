import os
from typing import Dict

import azure.servicebus as azure_servicebus
import azure.servicebus.aio as azure_servicebus_aio
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.trace import Pin

from .utils import create_context
from .utils import handle_service_bus_message_arg


config._add(
    "azure_servicebus",
    dict(
        _default_service=schematize_service_name("azure_servicebus"),
        distributed_tracing=asbool(os.getenv("DD_AZURE_SERVICEBUS_DISTRIBUTED_TRACING", default=True)),
    ),
)


def get_version() -> str:
    return getattr(azure_servicebus, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"azure.servicebus": ">=7.14.0"}


def patch():
    for azure_servicebus_module in (azure_servicebus, azure_servicebus_aio):
        _patch(azure_servicebus_module)


def _patch(azure_servicebus_module):
    """
    Patch `azure.servicebus` modules for tracing
    """
    # Check to see if we have patched module yet or not
    if getattr(azure_servicebus_module, "_datadog_patch", False):
        return
    azure_servicebus_module._datadog_patch = True

    if azure_servicebus_module.__name__ == "azure.servicebus.aio":
        Pin().onto(azure_servicebus_aio.ServiceBusSender)
        _w("azure.servicebus.aio", "ServiceBusSender.send_messages", _patched_send_messages_async)
        _w("azure.servicebus.aio", "ServiceBusSender.schedule_messages", _patched_schedule_messages_async)
    else:
        Pin().onto(azure_servicebus_module.ServiceBusSender)
        _w("azure.servicebus", "ServiceBusSender.send_messages", _patched_send_messages)
        _w("azure.servicebus", "ServiceBusSender.schedule_messages", _patched_schedule_messages)


def _patched_send_messages(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    resource_name = instance.entity_name

    with create_context("azure.servicebus.patched_producer", pin, resource_name) as ctx, ctx.span:
        if config.azure_servicebus.distributed_tracing:
            message_arg_value = get_argument_value(args, kwargs, 0, "message", True)
            handle_service_bus_message_arg(ctx.span, message_arg_value)
        core.dispatch(
            "azure.servicebus.send_message_modifier",
            (ctx, config.azure_servicebus, resource_name, instance.fully_qualified_namespace),
        )

        return wrapped(*args, **kwargs)


async def _patched_send_messages_async(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await wrapped(*args, **kwargs)

    resource_name = instance.entity_name

    with create_context("azure.servicebus.patched_producer", pin, resource_name) as ctx, ctx.span:
        if config.azure_servicebus.distributed_tracing:
            message_arg_value = get_argument_value(args, kwargs, 0, "message", True)
            handle_service_bus_message_arg(ctx.span, message_arg_value)
        core.dispatch(
            "azure.servicebus.send_message_modifier",
            (ctx, config.azure_servicebus, resource_name, instance.fully_qualified_namespace),
        )

        return await wrapped(*args, **kwargs)


def _patched_schedule_messages(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    resource_name = instance.entity_name

    with create_context("azure.servicebus.patched_producer", pin, resource_name) as ctx, ctx.span:
        if config.azure_servicebus.distributed_tracing:
            message_arg_value = get_argument_value(args, kwargs, 0, "messages", True)
            handle_service_bus_message_arg(ctx.span, message_arg_value)
        core.dispatch(
            "azure.servicebus.send_message_modifier",
            (ctx, config.azure_servicebus, resource_name, instance.fully_qualified_namespace),
        )

        return wrapped(*args, **kwargs)


async def _patched_schedule_messages_async(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await wrapped(*args, **kwargs)

    resource_name = instance.entity_name

    with create_context("azure.servicebus.patched_producer", pin, resource_name) as ctx, ctx.span:
        if config.azure_servicebus.distributed_tracing:
            message_arg_value = get_argument_value(args, kwargs, 0, "messages", True)
            handle_service_bus_message_arg(ctx.span, message_arg_value)
        core.dispatch(
            "azure.servicebus.send_message_modifier",
            (ctx, config.azure_servicebus, resource_name, instance.fully_qualified_namespace),
        )

        return await wrapped(*args, **kwargs)


def unpatch():
    for azure_servicebus_module in (azure_servicebus, azure_servicebus_aio):
        _unpatch(azure_servicebus_module)


def _unpatch(azure_servicebus_module):
    if not getattr(azure_servicebus_module, "_datadog_patch", False):
        return
    azure_servicebus_module._datadog_patch = False

    _u(azure_servicebus_module.ServiceBusSender, "send_messages")
    _u(azure_servicebus_module.ServiceBusSender, "schedule_messages")
