import azure.servicebus as azure_servicebus
import azure.servicebus.amqp as azure_servicebus_amqp

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.contrib.trace_utils import ext_service
from ddtrace.ext import SpanTypes
from ddtrace.ext import azure_servicebus as azure_servicebusx
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection


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


def get_application_properties(message_arg_value):
    if isinstance(message_arg_value, (azure_servicebus.ServiceBusMessage, azure_servicebus_amqp.AmqpAnnotatedMessage)):
        message = message_arg_value
        application_properties = message.application_properties
    elif (
        isinstance(message_arg_value, list)
        and message_arg_value
        and isinstance(
            message_arg_value[0], (azure_servicebus.ServiceBusMessage, azure_servicebus_amqp.AmqpAnnotatedMessage)
        )
    ):
        # TODO: only inject context on first message? Apparently this is an OTel standard
        message = message_arg_value[0]
        application_properties = message.application_properties
    else:
        application_properties = {}

    return application_properties
