import functools
import inspect
from typing import Any
from typing import Callable
from typing import Coroutine
from typing import Optional
from typing import Tuple
from typing import Union

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_cloud_faas_operation


def create_context(
    context_name: str, pin: Pin, resource: Optional[str] = None, headers: Optional[dict] = None
) -> core.ExecutionContext:
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


def wrap_function_with_tracing(
    func: Callable[..., Any],
    context_factory: Callable[[Any], core.ExecutionContext],
    pre_dispatch: Optional[Callable[[core.ExecutionContext, Any], Tuple[str, Tuple[Any, ...]]]] = None,
    post_dispatch: Optional[Callable[[core.ExecutionContext, Any], Tuple[str, Tuple[Any, ...]]]] = None,
) -> Union[Callable[..., Any], Callable[..., Coroutine[Any, Any, Any]]]:
    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            with context_factory(kwargs) as ctx:
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
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with context_factory(kwargs) as ctx:
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
