import functools
import inspect
from typing import List

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


def message_list_has_single_context(msg_list: List[azure_functions.ServiceBusMessage]):
    first_context = HTTPPropagator.extract(msg_list[0].application_properties)
    for message in msg_list[1:]:
        context = HTTPPropagator.extract(message.application_properties)
        if first_context != context:
            return False

    return True
