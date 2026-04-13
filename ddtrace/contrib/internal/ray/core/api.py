import sys

from ddtrace import config
from ddtrace import tracer
from ddtrace.contrib._events.ray import RayCoreAPIEvent
from ddtrace.contrib.internal.ray.core.utils import _extract_tracing_context_from_env
from ddtrace.contrib.internal.ray.core.utils import _get_ray_service_name
from ddtrace.internal import core
from ddtrace.internal.utils import get_argument_value


def traced_get(wrapped, instance, args, kwargs):
    """
    Trace the calls of ray.get
    """
    if not config.ray.trace_core_api:
        return wrapped(*args, **kwargs)

    timeout = kwargs.get("timeout")
    get_value = get_argument_value(args, kwargs, 0, "object_refs")

    with core.context_with_event(
        RayCoreAPIEvent(
            component=config.ray.integration_name,
            integration_config=config.ray,
            service=_get_ray_service_name(),
            api_name="ray.get",
            is_long_running=True,
            timeout_s=timeout,
            get_value_size_bytes=sys.getsizeof(get_value),
            activate=True,
            use_active_context=tracer.current_span() is not None,
            distributed_context=_extract_tracing_context_from_env() if tracer.current_span() is None else None,
        )
    ):
        return wrapped(*args, **kwargs)


def traced_put(wrapped, instance, args, kwargs):
    """
    Trace the calls of ray.put
    """
    if not config.ray.trace_core_api:
        return wrapped(*args, **kwargs)

    put_value = get_argument_value(args, kwargs, 0, "value")

    with core.context_with_event(
        RayCoreAPIEvent(
            component=config.ray.integration_name,
            integration_config=config.ray,
            service=_get_ray_service_name(),
            api_name="ray.put",
            is_long_running=False,
            put_value_type=str(type(put_value).__name__),
            put_value_size_bytes=sys.getsizeof(put_value),
            activate=True,
            use_active_context=tracer.current_span() is not None,
            distributed_context=_extract_tracing_context_from_env() if tracer.current_span() is None else None,
        )
    ):
        return wrapped(*args, **kwargs)


def traced_wait(wrapped, instance, args, kwargs):
    """
    Trace the calls of ray.wait
    """
    if not config.ray.trace_core_api:
        return wrapped(*args, **kwargs)

    timeout = kwargs.get("timeout")
    num_returns = kwargs.get("num_returns")
    fetch_local = kwargs.get("fetch_local")

    with core.context_with_event(
        RayCoreAPIEvent(
            component=config.ray.integration_name,
            integration_config=config.ray,
            service=_get_ray_service_name(),
            api_name="ray.wait",
            is_long_running=True,
            timeout_s=timeout,
            num_returns=num_returns,
            fetch_local=fetch_local,
            activate=True,
            use_active_context=tracer.current_span() is not None,
            distributed_context=_extract_tracing_context_from_env() if tracer.current_span() is None else None,
        )
    ):
        return wrapped(*args, **kwargs)
