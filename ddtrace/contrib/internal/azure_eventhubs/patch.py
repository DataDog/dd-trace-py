from typing import Dict

import azure.eventhub as azure_eventhub
import azure.eventhub.aio as azure_eventhub_aio
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.ext import azure_eventhubs as azure_eventhubsx
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.settings._config import _get_config

from .utils import create_context
from .utils import dispatch_message_modifier


config._add(
    "azure_eventhubs",
    dict(
        _default_service=schematize_service_name("azure_eventhubs"),
        distributed_tracing=asbool(_get_config("DD_AZURE_EVENTHUBS_DISTRIBUTED_TRACING", default=True)),
        batch_links=asbool(_get_config("DD_TRACE_AZURE_EVENTHUBS_BATCH_LINKS_ENABLED", default=True)),
    ),
)


def get_version() -> str:
    return getattr(azure_eventhub, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"azure.eventhub": ">=5.12.0"}


def patch():
    for azure_eventhubs_module in (azure_eventhub, azure_eventhub_aio):
        _patch(azure_eventhubs_module)


def _patch(azure_eventhubs_module):
    """
    Patch `azure.eventhub` modules for tracing
    """
    # Check to see if we have patched module yet or not
    if getattr(azure_eventhubs_module, "_datadog_patch", False):
        return
    azure_eventhubs_module._datadog_patch = True

    if azure_eventhubs_module.__name__ == "azure.eventhub.aio":
        Pin().onto(azure_eventhubs_module.EventHubProducerClient)
        _w("azure.eventhub.aio", "EventHubProducerClient.__init__", _patched_producer_init)
        _w("azure.eventhub.aio", "EventHubProducerClient.create_batch", _patched_create_batch_async)
        _w("azure.eventhub.aio", "EventHubProducerClient.send_event", _patched_send_event_async)
        _w("azure.eventhub.aio", "EventHubProducerClient.send_batch", _patched_send_batch_async)
        _w("azure.eventhub.aio", "EventHubProducerClient._buffered_send_event", _patched_send_event_async)
        _w("azure.eventhub.aio", "EventHubProducerClient._buffered_send_batch", _patched_send_batch_async)
    else:
        Pin().onto(azure_eventhubs_module.EventHubProducerClient)
        Pin().onto(azure_eventhubs_module.EventDataBatch)
        _w("azure.eventhub", "EventDataBatch.add", _patched_add)
        _w("azure.eventhub", "EventHubProducerClient.__init__", _patched_producer_init)
        _w("azure.eventhub", "EventHubProducerClient.create_batch", _patched_create_batch)
        _w("azure.eventhub", "EventHubProducerClient.send_event", _patched_send_event)
        _w("azure.eventhub", "EventHubProducerClient.send_batch", _patched_send_batch)
        _w("azure.eventhub", "EventHubProducerClient._buffered_send_event", _patched_send_event)
        _w("azure.eventhub", "EventHubProducerClient._buffered_send_batch", _patched_send_batch)


def _patched_producer_init(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    instance._dd_fully_qualified_namespace = get_argument_value(args, kwargs, 0, "fully_qualified_namespace", True)

    return wrapped(*args, **kwargs)


def _patched_create_batch(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled() or not config.azure_eventhubs.batch_links:
        return wrapped(*args, **kwargs)

    batch = wrapped(*args, **kwargs)

    batch._dd_eventhub_name = instance.eventhub_name
    batch._dd_fully_qualified_namespace = instance._dd_fully_qualified_namespace

    return batch


async def _patched_create_batch_async(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled() or not config.azure_eventhubs.batch_links:
        return await wrapped(*args, **kwargs)

    batch = await wrapped(*args, **kwargs)

    batch._dd_eventhub_name = instance.eventhub_name
    batch._dd_fully_qualified_namespace = instance._dd_fully_qualified_namespace

    return batch


def _patched_add(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if (
        not pin
        or not pin.enabled()
        or not config.azure_eventhubs.batch_links
        # Skip patching when these attributes haven't been added.
        # A known case is when a producer client in buffered mode
        # instantiates a batch without using the create_batch method.
        or not getattr(instance, "_dd_eventhub_name", None)
        or not getattr(instance, "_dd_fully_qualified_namespace", None)
    ):
        return wrapped(*args, **kwargs)

    resource_name = instance._dd_eventhub_name
    fully_qualified_namespace = instance._dd_fully_qualified_namespace
    operation_name = f"{azure_eventhubsx.CLOUD}.{azure_eventhubsx.SERVICE}.{azure_eventhubsx.CREATE}"

    with create_context("azure.eventhubs.patched_producer_batch", pin, operation_name, resource_name) as ctx:
        dispatch_message_modifier(
            ctx, args, kwargs, azure_eventhubsx.CREATE, resource_name, fully_qualified_namespace, "event_data"
        )
        return wrapped(*args, **kwargs)


def _patched_send_event(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    resource_name = instance.eventhub_name
    fully_qualified_namespace = instance._dd_fully_qualified_namespace
    operation_name = f"{azure_eventhubsx.CLOUD}.{azure_eventhubsx.SERVICE}.{azure_eventhubsx.SEND}"

    with create_context("azure.eventhubs.patched_producer_send", pin, operation_name, resource_name) as ctx:
        dispatch_message_modifier(
            ctx, args, kwargs, azure_eventhubsx.SEND, resource_name, fully_qualified_namespace, "event_data"
        )
        return wrapped(*args, **kwargs)


async def _patched_send_event_async(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await wrapped(*args, **kwargs)

    resource_name = instance.eventhub_name
    fully_qualified_namespace = instance._dd_fully_qualified_namespace
    operation_name = f"{azure_eventhubsx.CLOUD}.{azure_eventhubsx.SERVICE}.{azure_eventhubsx.SEND}"

    with create_context("azure.eventhubs.patched_producer_send", pin, operation_name, resource_name) as ctx:
        dispatch_message_modifier(
            ctx, args, kwargs, azure_eventhubsx.SEND, resource_name, fully_qualified_namespace, "event_data"
        )
        return await wrapped(*args, **kwargs)


def _patched_send_batch(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    resource_name = instance.eventhub_name
    fully_qualified_namespace = instance._dd_fully_qualified_namespace
    operation_name = f"{azure_eventhubsx.CLOUD}.{azure_eventhubsx.SERVICE}.{azure_eventhubsx.SEND}"

    with create_context("azure.eventhubs.patched_producer_send_batch", pin, operation_name, resource_name) as ctx:
        dispatch_message_modifier(
            ctx, args, kwargs, azure_eventhubsx.SEND, resource_name, fully_qualified_namespace, "event_data_batch"
        )
        return wrapped(*args, **kwargs)


async def _patched_send_batch_async(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await wrapped(*args, **kwargs)

    resource_name = instance.eventhub_name
    fully_qualified_namespace = instance._dd_fully_qualified_namespace
    operation_name = f"{azure_eventhubsx.CLOUD}.{azure_eventhubsx.SERVICE}.{azure_eventhubsx.SEND}"

    with create_context("azure.eventhubs.patched_producer_send_batch", pin, operation_name, resource_name) as ctx:
        dispatch_message_modifier(
            ctx, args, kwargs, azure_eventhubsx.SEND, resource_name, fully_qualified_namespace, "event_data_batch"
        )
        return await wrapped(*args, **kwargs)


def unpatch():
    for azure_eventhubs_module in (azure_eventhub, azure_eventhub_aio):
        _unpatch(azure_eventhubs_module)


def _unpatch(azure_eventhubs_module):
    if not getattr(azure_eventhubs_module, "_datadog_patch", False):
        return
    azure_eventhubs_module._datadog_patch = False

    _u(azure_eventhubs_module.EventHubProducerClient, "__init__")
    _u(azure_eventhubs_module.EventHubProducerClient, "create_batch")
    _u(azure_eventhubs_module.EventHubProducerClient, "send_event")
    _u(azure_eventhubs_module.EventHubProducerClient, "send_batch")
    _u(azure_eventhubs_module.EventHubProducerClient, "_buffered_send_event")
    _u(azure_eventhubs_module.EventHubProducerClient, "_buffered_send_batch")

    if azure_eventhubs_module.__name__ == "azure.eventhub":
        _u(azure_eventhubs_module.EventDataBatch, "add")
