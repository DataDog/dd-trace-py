import os

import azure.servicebus as azure_servicebus
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.contrib.trace_utils import ext_service
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import azure_servicebus as azure_servicebusx
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import Pin


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
    _w("azure.servicebus", "ServiceBusSender.send_messages", _patched_test)


def _patched_test(wrapped, instance, args, kwargs):
    # TODO: rename CONNECTION_SETTING to CONNECTION_STRING
    # TODO: is this a queue or topic sender? Does it change the span attributes?

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    operation_name = schematize_messaging_operation(
        azure_servicebusx.PRODUCE, provider="azure_servicebus", direction=SpanDirection.OUTBOUND
    )

    with pin.tracer.trace(
        name=operation_name,
        service=ext_service(pin, config.azure_servicebus),
        span_type=SpanTypes.WORKER,
    ) as span:
        span.set_tag_str(COMPONENT, config.azure_servicebus.integration_name)
        span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)

        # TODO: check if distributed tracing is enabled
        # TODO: only inject context on first message? Apparently this is an OTel standard
        # TODO: handle all possible types passed - single message, message batch, list of messages, ampq messages
        message = get_argument_value(args, kwargs, 0, "message", True) or None
        application_properties = message.application_properties or {}
        HTTPPropagator.inject(span.context, application_properties)
        message.application_properties = application_properties

        return wrapped(*args, **kwargs)


def unpatch():
    if not getattr(azure_servicebus, "_datadog_patch", False):
        return
    azure_servicebus._datadog_patch = False

    _u(azure_servicebus.ServiceBusSender, "send_messages")
    # TODO: add remaining methods to unpatch
