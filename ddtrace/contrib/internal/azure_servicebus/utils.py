from typing import Any
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union
from uuid import UUID

import azure.servicebus as azure_servicebus
from azure.servicebus import ServiceBusMessage
from azure.servicebus import ServiceBusMessageBatch
import azure.servicebus.amqp as azure_servicebus_amqp
from azure.servicebus.amqp import AmqpAnnotatedMessage

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace._trace.span import Span
from ddtrace.contrib.trace_utils import ext_service
from ddtrace.ext import SpanTypes
from ddtrace.ext import azure_servicebus as azure_servicebusx
from ddtrace.internal import core
from ddtrace.internal.utils import get_argument_value
from ddtrace.propagation.http import HTTPPropagator


def create_context(
    context_name: str,
    pin: Pin,
    operation_name: str,
    resource: Optional[str] = None,
) -> core.ExecutionContext:
    return core.context_with_data(
        context_name,
        span_name=operation_name,
        pin=pin,
        resource=resource,
        service=ext_service(pin, config.azure_servicebus),
        span_type=SpanTypes.WORKER,
    )


def handle_service_bus_message_context(
    span: Span,
    message_arg_value: Union[
        ServiceBusMessage,
        AmqpAnnotatedMessage,
        List[Union[ServiceBusMessage, AmqpAnnotatedMessage]],
        ServiceBusMessageBatch,
    ],
):
    if isinstance(message_arg_value, (ServiceBusMessage, AmqpAnnotatedMessage)):
        inject_context(span, message_arg_value)
    elif (
        isinstance(message_arg_value, list)
        and message_arg_value
        and isinstance(message_arg_value[0], (ServiceBusMessage, AmqpAnnotatedMessage))
    ):
        for message in message_arg_value:
            inject_context(span, message)
    elif isinstance(message_arg_value, azure_servicebus.ServiceBusMessageBatch):
        for message in message_arg_value._messages:
            parent_context = HTTPPropagator.extract(message._message.application_properties)
            if parent_context.trace_id is not None and parent_context.span_id is not None:
                span.link_span(parent_context)


def inject_context(span: Span, message: Union[ServiceBusMessage, AmqpAnnotatedMessage]):
    """
    ServiceBusMessage.application_properties is of type Dict[str | bytes, PrimitiveTypes] | None
    AmqpAnnotatedMessage.application_properties is of type Dict[str | bytes, Any] | None
    while HTTPPropagator.inject expects type of Dict[str, str].

    Inject the context into an empty dictionary and merge it with application_properties
    to preserve the original type.
    """
    inject_carrier = {}
    HTTPPropagator.inject(span.context, inject_carrier)

    # Set application_properties to empty dictionary if None
    if not message.application_properties:
        message.application_properties = {}

    message.application_properties.update(inject_carrier)


def handle_service_bus_message_attributes(
    message_arg_value: Union[
        ServiceBusMessage,
        AmqpAnnotatedMessage,
        List[Union[ServiceBusMessage, AmqpAnnotatedMessage]],
        ServiceBusMessageBatch,
    ],
) -> Tuple[Union[str, None], Union[str, None]]:
    if isinstance(message_arg_value, azure_servicebus.ServiceBusMessage):
        batch_count = None
        message_id = message_arg_value.message_id
    elif isinstance(message_arg_value, azure_servicebus_amqp.AmqpAnnotatedMessage):
        batch_count = None
        message_id_raw: Union[str, bytes, UUID, None] = getattr(message_arg_value.properties, "message_id", None)

        # stringify bytes/UUID, strip whitespace in strings, and map empty strings to None
        if message_id_raw:
            message_id = str(message_id_raw).strip() or None
        else:
            message_id = None
    elif isinstance(message_arg_value, azure_servicebus.ServiceBusMessageBatch):
        batch_count = str(len(message_arg_value._messages))
        message_id = None
    elif isinstance(message_arg_value, list):
        batch_count = str(len(message_arg_value))
        message_id = None
    return message_id, batch_count


def dispatch_message_modifier(
    ctx: core.ExecutionContext,
    args: Any,
    kwargs: Any,
    message_operation: str,
    resource_name: str,
    fully_qualified_namespace: str,
    message_arg: str,
):
    message_arg_value = get_argument_value(args, kwargs, 0, message_arg, True)
    if message_arg_value is None:
        return

    message_id, batch_count = handle_service_bus_message_attributes(message_arg_value)

    if config.azure_servicebus.distributed_tracing:
        handle_service_bus_message_context(ctx.span, message_arg_value)

    core.dispatch(
        "azure.servicebus.message_modifier",
        (
            ctx,
            config.azure_servicebus,
            message_operation,
            azure_servicebusx.SERVICE,
            resource_name,
            fully_qualified_namespace,
            message_id,
            batch_count,
        ),
    )
