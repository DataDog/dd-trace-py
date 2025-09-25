from typing import Dict

import azure.servicebus as azure_servicebus
import azure.servicebus.aio as azure_servicebus_aio
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.ext import azure_servicebus as azure_servicebusx
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.formats import asbool
from ddtrace.settings._config import _get_config

from .utils import create_context
from .utils import dispatch_message_modifier


config._add(
    "azure_servicebus",
    dict(
        _default_service=schematize_service_name("azure_servicebus"),
        distributed_tracing=asbool(_get_config("DD_AZURE_SERVICEBUS_DISTRIBUTED_TRACING", default=True)),
        batch_links=asbool(_get_config("DD_TRACE_AZURE_SERVICEBUS_BATCH_LINKS_ENABLED", default=True)),
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
        Pin().onto(azure_servicebus_module.ServiceBusSender)
        _w("azure.servicebus.aio", "ServiceBusSender.create_message_batch", _patched_create_message_batch_async)
        _w("azure.servicebus.aio", "ServiceBusSender.send_messages", _patched_send_messages_async)
        _w("azure.servicebus.aio", "ServiceBusSender.schedule_messages", _patched_schedule_messages_async)
    else:
        Pin().onto(azure_servicebus_module.ServiceBusSender)
        Pin().onto(azure_servicebus_module.ServiceBusMessageBatch)
        _w("azure.servicebus", "ServiceBusMessageBatch.add_message", _patched_add_message)
        _w("azure.servicebus", "ServiceBusSender.create_message_batch", _patched_create_message_batch)
        _w("azure.servicebus", "ServiceBusSender.send_messages", _patched_send_messages)
        _w("azure.servicebus", "ServiceBusSender.schedule_messages", _patched_schedule_messages)


def _patched_create_message_batch(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled() or not config.azure_servicebus.batch_links:
        return wrapped(*args, **kwargs)

    batch = wrapped(*args, **kwargs)

    batch._dd_entity_name = instance.entity_name
    batch._dd_fully_qualified_namespace = instance.fully_qualified_namespace

    return batch


async def _patched_create_message_batch_async(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled() or not config.azure_servicebus.batch_links:
        return await wrapped(*args, **kwargs)

    batch = await wrapped(*args, **kwargs)

    batch._dd_entity_name = instance.entity_name
    batch._dd_fully_qualified_namespace = instance.fully_qualified_namespace

    return batch


def _patched_add_message(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled() or not config.azure_servicebus.batch_links:
        return wrapped(*args, **kwargs)

    resource_name = instance._dd_entity_name
    fully_qualified_namespace = instance._dd_fully_qualified_namespace
    operation_name = f"{azure_servicebusx.CLOUD}.{azure_servicebusx.SERVICE}.{azure_servicebusx.CREATE}"

    with create_context("azure.servicebus.patched_producer_batch", pin, operation_name, resource_name) as ctx, ctx.span:
        dispatch_message_modifier(
            ctx, args, kwargs, azure_servicebusx.CREATE, resource_name, fully_qualified_namespace, "message"
        )
        return wrapped(*args, **kwargs)


def _patched_send_messages(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    resource_name = instance.entity_name
    fully_qualified_namespace = instance.fully_qualified_namespace
    operation_name = f"{azure_servicebusx.CLOUD}.{azure_servicebusx.SERVICE}.{azure_servicebusx.SEND}"

    with create_context("azure.servicebus.patched_producer_send", pin, operation_name, resource_name) as ctx, ctx.span:
        dispatch_message_modifier(
            ctx, args, kwargs, azure_servicebusx.SEND, resource_name, fully_qualified_namespace, "message"
        )
        return wrapped(*args, **kwargs)


async def _patched_send_messages_async(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await wrapped(*args, **kwargs)

    resource_name = instance.entity_name
    fully_qualified_namespace = instance.fully_qualified_namespace
    operation_name = f"{azure_servicebusx.CLOUD}.{azure_servicebusx.SERVICE}.{azure_servicebusx.SEND}"

    with create_context("azure.servicebus.patched_producer_send", pin, operation_name, resource_name) as ctx, ctx.span:
        dispatch_message_modifier(
            ctx, args, kwargs, azure_servicebusx.SEND, resource_name, fully_qualified_namespace, "message"
        )
        return await wrapped(*args, **kwargs)


def _patched_schedule_messages(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    resource_name = instance.entity_name
    fully_qualified_namespace = instance.fully_qualified_namespace
    operation_name = f"{azure_servicebusx.CLOUD}.{azure_servicebusx.SERVICE}.{azure_servicebusx.SEND}"

    with create_context(
        "azure.servicebus.patched_producer_schedule", pin, operation_name, resource_name
    ) as ctx, ctx.span:
        dispatch_message_modifier(
            ctx, args, kwargs, azure_servicebusx.SEND, resource_name, fully_qualified_namespace, "messages"
        )
        return wrapped(*args, **kwargs)


async def _patched_schedule_messages_async(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await wrapped(*args, **kwargs)

    resource_name = instance.entity_name
    fully_qualified_namespace = instance.fully_qualified_namespace
    operation_name = f"{azure_servicebusx.CLOUD}.{azure_servicebusx.SERVICE}.{azure_servicebusx.SEND}"

    with create_context(
        "azure.servicebus.patched_producer_schedule", pin, operation_name, resource_name
    ) as ctx, ctx.span:
        dispatch_message_modifier(
            ctx, args, kwargs, azure_servicebusx.SEND, resource_name, fully_qualified_namespace, "messages"
        )
        return await wrapped(*args, **kwargs)


def unpatch():
    for azure_servicebus_module in (azure_servicebus, azure_servicebus_aio):
        _unpatch(azure_servicebus_module)


def _unpatch(azure_servicebus_module):
    if not getattr(azure_servicebus_module, "_datadog_patch", False):
        return
    azure_servicebus_module._datadog_patch = False

    _u(azure_servicebus_module.ServiceBusSender, "create_message_batch")
    _u(azure_servicebus_module.ServiceBusSender, "send_messages")
    _u(azure_servicebus_module.ServiceBusSender, "schedule_messages")

    if azure_servicebus_module.__name__ == "azure.servicebus":
        _u(azure_servicebus_module.ServiceBusMessageBatch, "add_message")
