from ddtrace import config
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_cloud_faas_operation


def create_context(context_name, pin, resource=None):
    operation_name = schematize_cloud_faas_operation(
        "azure.durable_functions.invoke", cloud_provider="azure", cloud_service="functions"
    )
    return core.context_with_data(
        context_name,
        span_name=operation_name,
        pin=pin,
        resource=resource,
        service=int_service(pin, config.azure_durable_functions),
        span_type=SpanTypes.SERVERLESS,
        integration_config=config.azure_durable_functions,
    )
