import functools
import inspect
from typing import List
from typing import Union

import azure.functions as azure_functions

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_cloud_faas_operation
from ddtrace.propagation.http import HTTPPropagator


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


def wrap_function_with_tracing(func, context_factory, pre_dispatch=None, post_dispatch=None):
    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            with context_factory(kwargs) as ctx, ctx.span:
                if pre_dispatch:
                    core.dispatch(*pre_dispatch(ctx, kwargs))

                res = None
                try:
                    res = await func(*args, **kwargs)
                    return res
                finally:
                    if post_dispatch:
                        core.dispatch(*post_dispatch(ctx, res))

        return async_wrapper

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with context_factory(kwargs) as ctx, ctx.span:
            events = kwargs.get("event")
            print(f"events: {events}")
            print(f"kwargs: {kwargs}")
            if events is not None:
                for event in events:
                    print(f"event: {event}")
                    print(f"event.metadata: {event.metadata}")
                    print(f"context properties: {get_properties(event)}")
                    parent_context = HTTPPropagator.extract(get_properties(event))
                    print(f"parent_context: {parent_context}")
                    ctx.span.link_span(parent_context)
                    print(f"span: {ctx.span}")
            if pre_dispatch:
                core.dispatch(*pre_dispatch(ctx, kwargs))

            res = None
            try:
                res = func(*args, **kwargs)
                return res
            finally:
                if post_dispatch:
                    core.dispatch(*post_dispatch(ctx, res))

    return wrapper


def get_properties(message: Union[azure_functions.ServiceBusMessage, azure_functions.EventHubEvent]):
    if isinstance(message, azure_functions.ServiceBusMessage):
        return message.application_properties
    if isinstance(message, azure_functions.EventHubEvent) and message.metadata is not None:
        properties = message.metadata.get("Properties")
        if properties is not None:
            return message.metadata.get("Properties")
        properties_array = message.metadata.get("PropertiesArray")
        if properties_array is not None and len(properties_array) > 0:
            return properties_array[0]
    return {}


def message_list_has_single_context(
    msg_list: List[Union[azure_functions.ServiceBusMessage, azure_functions.EventHubEvent]],
):
    first_context = HTTPPropagator.extract(get_properties(msg_list[0]))
    for message in msg_list[1:]:
        context = HTTPPropagator.extract(get_properties(message))
        if first_context != context:
            return False

    return True
