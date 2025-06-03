import functools
import inspect

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
