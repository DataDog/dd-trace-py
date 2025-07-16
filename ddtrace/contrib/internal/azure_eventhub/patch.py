import os
from typing import Dict

import azure.eventhub as azure_eventhub
import azure.eventhub.aio as azure_eventhub_aio
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.ext import azure_eventhub as azure_eventhubx
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.trace import Pin

from .utils import create_context
from .utils import handle_event_hub_event_data_arg


config._add(
    "azure_eventhub",
    dict(
        _default_service=schematize_service_name("azure_eventhub"),
        distributed_tracing=asbool(os.getenv("DD_AZURE_EVENTHUB_DISTRIBUTED_TRACING", default=True)),
    ),
)


def get_version() -> str:
    return getattr(azure_eventhub, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"azure.eventhub": ">=5.15.0"}  # TODO: determine minimum version


def patch():
    for azure_eventhub_module in (azure_eventhub, azure_eventhub_aio):
        _patch(azure_eventhub_module)


def _patch(azure_eventhub_module):
    """
    Patch `azure.eventhub` modules for tracing
    """
    # Check to see if we have patched module yet or not
    if getattr(azure_eventhub_module, "_datadog_patch", False):
        return
    azure_eventhub_module._datadog_patch = True

    if azure_eventhub_module.__name__ == "azure.eventhub.aio":
        Pin().onto(azure_eventhub_aio.EventHubProducerClient)
        _w("azure.eventhub.aio", "EventHubProducerClient.__init__", _patched_producer_init)  # TODO: test async init
        _w("azure.eventhub.aio", "EventHubProducerClient.send_event", _patched_send_event_async)
        _w("azure.eventhub.aio", "EventHubProducerClient.send_batch", _patched_send_batch_async)
    else:
        Pin().onto(azure_eventhub_module.EventHubProducerClient)
        _w("azure.eventhub", "EventHubProducerClient.__init__", _patched_producer_init)
        _w("azure.eventhub", "EventHubProducerClient.send_event", _patched_send_event)
        _w("azure.eventhub", "EventHubProducerClient.send_batch", _patched_send_batch)


def _patched_producer_init(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    # TODO: test positional argument
    instance._dd_fully_qualified_namespace = get_argument_value(args, kwargs, 0, "fully_qualified_namespace", True)

    return wrapped(*args, **kwargs)


def _patched_send_event(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    resource_name = instance.eventhub_name

    with create_context("azure.eventhub.patched_producer", pin, resource_name) as ctx, ctx.span:
        if config.azure_eventhub.distributed_tracing:
            event_data_arg_value = get_argument_value(args, kwargs, 0, "event_data", True)
            handle_event_hub_event_data_arg(ctx.span, event_data_arg_value)
        core.dispatch(
            "azure.eventhub.send_event_modifier",
            (
                ctx,
                config.azure_eventhub,
                azure_eventhubx.SERVICE,
                resource_name,
                instance._dd_fully_qualified_namespace,
            ),
        )

        return wrapped(*args, **kwargs)


async def _patched_send_event_async(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await wrapped(*args, **kwargs)

    resource_name = instance.eventhub_name

    with create_context("azure.eventhub.patched_producer", pin, resource_name) as ctx, ctx.span:
        if config.azure_eventhub.distributed_tracing:
            event_data_arg_value = get_argument_value(args, kwargs, 0, "event_data", True)
            handle_event_hub_event_data_arg(ctx.span, event_data_arg_value)
        core.dispatch(
            "azure.eventhub.send_event_modifier",
            (
                ctx,
                config.azure_eventhub,
                azure_eventhubx.SERVICE,
                resource_name,
                instance._dd_fully_qualified_namespace,
            ),
        )

        return await wrapped(*args, **kwargs)


def _patched_send_batch(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    resource_name = instance.eventhub_name

    with create_context("azure.eventhub.patched_producer", pin, resource_name) as ctx, ctx.span:
        if config.azure_eventhub.distributed_tracing:
            event_data_arg_value = get_argument_value(args, kwargs, 0, "event_data_batch", True)
            handle_event_hub_event_data_arg(ctx.span, event_data_arg_value)
        core.dispatch(
            "azure.eventhub.send_event_modifier",
            (
                ctx,
                config.azure_eventhub,
                azure_eventhubx.SERVICE,
                resource_name,
                instance._dd_fully_qualified_namespace,
            ),
        )

        return wrapped(*args, **kwargs)


async def _patched_send_batch_async(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await wrapped(*args, **kwargs)

    resource_name = instance.eventhub_name

    with create_context("azure.eventhub.patched_producer", pin, resource_name) as ctx, ctx.span:
        if config.azure_eventhub.distributed_tracing:
            event_data_arg_value = get_argument_value(args, kwargs, 0, "event_data_batch", True)
            handle_event_hub_event_data_arg(ctx.span, event_data_arg_value)
        core.dispatch(
            "azure.eventhub.send_event_modifier",
            (
                ctx,
                config.azure_eventhub,
                azure_eventhubx.SERVICE,
                resource_name,
                instance._dd_fully_qualified_namespace,
            ),
        )

        return await wrapped(*args, **kwargs)


def unpatch():
    for azure_eventhub_module in (azure_eventhub, azure_eventhub_aio):
        _unpatch(azure_eventhub_module)


def _unpatch(azure_eventhub_module):
    if not getattr(azure_eventhub_module, "_datadog_patch", False):
        return
    azure_eventhub_module._datadog_patch = False

    _u(azure_eventhub_module.EventHubProducerClient, "__init__")
    _u(azure_eventhub_module.EventHubProducerClient, "send_event")
    _u(azure_eventhub_module.EventHubProducerClient, "send_batch")
