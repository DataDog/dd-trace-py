import azure.servicebus as azure_servicebus
import azure.servicebus.amqp as azure_servicebus_amqp

from ddtrace import config
from ddtrace.contrib.trace_utils import ext_service
from ddtrace.ext import SpanTypes
from ddtrace.ext import azure_servicebus as azure_servicebusx
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.propagation.http import HTTPPropagator


def create_context(context_name, pin):
    operation_name = schematize_messaging_operation(
        azure_servicebusx.PRODUCE, provider="azure_servicebus", direction=SpanDirection.OUTBOUND
    )
    return core.context_with_data(
        context_name,
        span_name=operation_name,
        pin=pin,
        service=ext_service(pin, config.azure_servicebus),
        span_type=SpanTypes.WORKER,
    )


def handle_service_bus_message_arg(span, message_arg_value):
    if isinstance(message_arg_value, (azure_servicebus.ServiceBusMessage, azure_servicebus_amqp.AmqpAnnotatedMessage)):
        message_arg_value.application_properties
        inject_context(span, message_arg_value)
    elif (
        isinstance(message_arg_value, list)
        and message_arg_value
        and isinstance(
            message_arg_value[0], (azure_servicebus.ServiceBusMessage, azure_servicebus_amqp.AmqpAnnotatedMessage)
        )
    ):
        for message in message_arg_value:
            inject_context(span, message)


def inject_context(span, message):
    """
    message.application_properties is of type Dict[str | bytes, PrimitiveTypes] | Dict[str | bytes, Any] | None
    while HTTPPropagator.inject expects type of Dict[str, str].

    Inject the context into an empty dictionary and merge it with message.application_properties
    to preserve the original type.
    """
    inject_carrier = {}
    HTTPPropagator.inject(span.context, inject_carrier)

    # Set application_properties to empty dictionary if None
    if not message.application_properties:
        message.application_properties = {}

    message.application_properties.update(inject_carrier)
