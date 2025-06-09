import os

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
from .utils import get_application_properties


config._add(
    "azure_servicebus",
    dict(
        _default_service=schematize_service_name("azure_servicebus"),
        distributed_tracing=asbool(os.getenv("DD_AZURE_SERVICEBUS_DISTRIBUTED_TRACING", default=True)),
    ),
)


def get_version():
    # type: () -> str
    return getattr(azure_servicebus, "__version__", "")


def patch():
    """
    Patch `azure.servicebus` module for tracing
    """
    # Check to see if we have patched azure.servicebus yet or not
    if getattr(azure_servicebus, "_datadog_patch", False):
        return
    azure_servicebus._datadog_patch = True

    Pin().onto(azure_servicebus.ServiceBusSender)
    _w("azure.servicebus", "ServiceBusSender.send_messages", _patched_send_messages)
    # TODO: patch schedule_messages

    # Check to see if we have patched azure.servicebus.aio yet or not
    if getattr(azure_servicebus_aio, "_datadog_patch", False):
        return
    azure_servicebus_aio._datadog_patch = True

    Pin().onto(azure_servicebus_aio.ServiceBusSender)
    _w("azure.servicebus.aio", "ServiceBusSender.send_messages", _patched_send_messages_async)
    # TODO: patch schedule_messages


def _patched_send_messages(wrapped, instance, args, kwargs):
    # TODO: is this a queue or topic sender? Does it change the span attributes?

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    with create_context("azure.servicebus.patched_producer", pin) as ctx, ctx.span:
        message_arg_value = get_argument_value(args, kwargs, 0, "message", True)
        application_properties = get_application_properties(message_arg_value)
        core.dispatch("azure.servicebus.send_message_modifier", (ctx, config.azure_servicebus, application_properties))

        return wrapped(*args, **kwargs)


async def _patched_send_messages_async(wrapped, instance, args, kwargs):
    # TODO: is this a queue or topic sender? Does it change the span attributes?

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await wrapped(*args, **kwargs)

    with create_context("azure.servicebus.patched_producer", pin) as ctx, ctx.span:
        message_arg_value = get_argument_value(args, kwargs, 0, "message", True)
        application_properties = get_application_properties(message_arg_value)
        core.dispatch("azure.servicebus.send_message_modifier", (ctx, config.azure_servicebus, application_properties))

        return await wrapped(*args, **kwargs)


def unpatch():
    if not getattr(azure_servicebus, "_datadog_patch", False):
        return
    azure_servicebus._datadog_patch = False

    _u(azure_servicebus.ServiceBusSender, "send_messages")
    _u(azure_servicebus_aio.ServiceBusSender, "send_messages")
    # TODO: add remaining methods to unpatch
