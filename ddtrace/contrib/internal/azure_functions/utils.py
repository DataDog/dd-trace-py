from ddtrace import config
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_cloud_faas_operation
from ddtrace.trace import Pin


def create_context(context_name, pin, resource=None, headers=None):
    operation_name = schematize_cloud_faas_operation(
        "azure.functions.invoke", cloud_provider="azure", cloud_service="functions"
    )
    return core.context_with_data(
        context_name,
        span_name=operation_name,
        pin=pin,
        resource=resource,
        service=int_service(pin, config.azure_functions),
        span_type=SpanTypes.SERVERLESS,
        distributed_headers=headers,
        integration_config=config.azure_functions,
        activate_distributed_headers=True,
    )


def get_function_name(pin, instance, func):
    if pin.tags and pin.tags.get("function_name"):
        function_name = pin.tags.get("function_name")
        Pin.override(instance, tags={"function_name": ""})
    else:
        function_name = func.__name__
    return function_name
