from typing import Union
from uuid import UUID

import azure.eventhub as azure_eventhub
import azure.eventhub.amqp as azure_eventhub_amqp

from ddtrace import config
from ddtrace.contrib.trace_utils import ext_service
from ddtrace.ext import SpanTypes
from ddtrace.ext import azure_eventhubs as azure_eventhubsx
from ddtrace.internal import core
from ddtrace.internal.utils import get_argument_value
from ddtrace.propagation.http import HTTPPropagator


def create_context(context_name, pin, operation_name, resource=None):
    return core.context_with_data(
        context_name,
        span_name=operation_name,
        pin=pin,
        resource=resource,
        service=ext_service(pin, config.azure_eventhubs),
        span_type=SpanTypes.WORKER,
    )


def handle_event_hubs_event_data_context(span, event_data_arg_value):
    if isinstance(event_data_arg_value, (azure_eventhub.EventData, azure_eventhub_amqp.AmqpAnnotatedMessage)):
        inject_context(span, event_data_arg_value)
    elif (
        isinstance(event_data_arg_value, list)
        and event_data_arg_value
        and isinstance(event_data_arg_value[0], (azure_eventhub.EventData, azure_eventhub_amqp.AmqpAnnotatedMessage))
    ):
        for event in event_data_arg_value:
            inject_context(span, event)
    elif isinstance(event_data_arg_value, azure_eventhub.EventDataBatch):
        for event_data in event_data_arg_value._internal_events:
            parent_context = extract_context(event_data)
            if (
                parent_context is not None
                and parent_context.trace_id is not None
                and parent_context.span_id is not None
            ):
                span.link_span(parent_context)


def extract_context(event_data):
    msg = event_data if isinstance(event_data, azure_eventhub_amqp.AmqpAnnotatedMessage) else event_data._message
    return HTTPPropagator.extract(msg.application_properties)


def inject_context(span, event_data):
    """
    EventData.properties is of type Dict[str | bytes, Any] | None
    AmqpAnnotatedMessage.application_properties is of type Dict[str | bytes, Any] | None
    while HTTPPropagator.inject expects type of Dict[str, str].

    Inject the context into an empty dictionary and merge it with properties or application_properties
    to preserve the original type.
    """
    inject_carrier = {}
    HTTPPropagator.inject(span.context, inject_carrier)

    if isinstance(event_data, azure_eventhub.EventData):
        # Set properties to empty dictionary if None
        if not event_data.properties:
            event_data.properties = {}

        event_data.properties.update(inject_carrier)
    elif isinstance(event_data, azure_eventhub_amqp.AmqpAnnotatedMessage):
        # Set application_properties to empty dictionary if None
        if not event_data.application_properties:
            event_data.application_properties = {}

        event_data.application_properties.update(inject_carrier)


def handle_event_data_attributes(event_data_arg_value):
    if isinstance(event_data_arg_value, azure_eventhub.EventData):
        batch_count = None
        message_id = event_data_arg_value.message_id
    elif isinstance(event_data_arg_value, azure_eventhub_amqp.AmqpAnnotatedMessage):
        batch_count = None
        message_id_raw: Union[str, bytes, UUID, None] = getattr(event_data_arg_value.properties, "message_id", None)

        # stringify bytes/UUID, strip whitespace in strings, and map empty strings to None
        if message_id_raw:
            message_id = str(message_id_raw).strip() or None
        else:
            message_id = None
    elif isinstance(event_data_arg_value, azure_eventhub.EventDataBatch):
        batch_count = str(len(event_data_arg_value._internal_events))
        message_id = None
    elif isinstance(event_data_arg_value, list):
        batch_count = str(len(event_data_arg_value))
        message_id = None
    else:
        message_id = None
        batch_count = None
    return message_id, batch_count


def dispatch_message_modifier(
    ctx, args, kwargs, message_operation, resource_name, fully_qualified_namespace, event_data_arg
):
    event_data_arg_value = get_argument_value(args, kwargs, 0, event_data_arg, True)
    message_id, batch_count = handle_event_data_attributes(event_data_arg_value)

    if config.azure_eventhubs.distributed_tracing:
        handle_event_hubs_event_data_context(ctx.span, event_data_arg_value)

    core.dispatch(
        "azure.eventhubs.message_modifier",
        (
            ctx,
            config.azure_eventhubs,
            message_operation,
            azure_eventhubsx.SERVICE,
            resource_name,
            fully_qualified_namespace,
            message_id,
            batch_count,
        ),
    )
