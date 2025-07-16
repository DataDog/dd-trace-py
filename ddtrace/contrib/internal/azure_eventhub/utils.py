import azure.eventhub as azure_eventhub
import azure.eventhub.amqp as azure_eventhub_amqp

from ddtrace import config
from ddtrace.contrib.trace_utils import ext_service
from ddtrace.ext import SpanTypes
from ddtrace.ext import azure_eventhub as azure_eventhubx
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_cloud_messaging_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.propagation.http import HTTPPropagator


def create_context(context_name, pin, resource=None):
    operation_name = schematize_cloud_messaging_operation(
        azure_eventhubx.PRODUCE, cloud_provider="azure", cloud_service="eventhub", direction=SpanDirection.OUTBOUND
    )
    return core.context_with_data(
        context_name,
        span_name=operation_name,
        pin=pin,
        resource=resource,
        service=ext_service(pin, config.azure_eventhub),
        span_type=SpanTypes.WORKER,
    )


def handle_event_hub_event_data_arg(span, event_data_arg_value):
    if isinstance(event_data_arg_value, (azure_eventhub.EventData, azure_eventhub_amqp.AmqpAnnotatedMessage)):
        inject_context(span, event_data_arg_value)
    elif (
        isinstance(event_data_arg_value, list)
        and event_data_arg_value
        and isinstance(event_data_arg_value[0], (azure_eventhub.EventData, azure_eventhub_amqp.AmqpAnnotatedMessage))
    ):
        for event_data in event_data_arg_value:
            inject_context(span, event_data)


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
