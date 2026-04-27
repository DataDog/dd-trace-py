from functools import wraps
import inspect

from ddtrace import config
from ddtrace import tracer
from ddtrace.contrib._events.ray import RayContextInjectionEvent
from ddtrace.contrib._events.ray import RayExecutionEvent
from ddtrace.contrib._events.ray import RaySubmissionEvent
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.propagation.http import _TraceContext

from ..constants import DD_RAY_TRACE_CTX
from .utils import _extract_tracing_context_from_env
from .utils import _get_ray_service_name
from .utils import _inject_dd_trace_ctx_kwarg
from .utils import extract_signature


log = get_logger(__name__)

RAY_TASK_MODULE_DENYLIST = {"ray.data._internal"}


def _wrap_remote_function_execution(function):
    """Inject trace context parameter into function signature"""

    @wraps(function)
    def wrapped_function(*args, **kwargs):
        return _wrap_task_execution(function, *args, **kwargs)

    return wrapped_function


def _wrap_task_execution(wrapped, *args, **kwargs):
    """
    Wraps the actual execution of a Ray task to trace its performance.
    """
    if DD_RAY_TRACE_CTX not in kwargs:
        return wrapped(*args, **kwargs)

    with core.context_with_event(
        RayExecutionEvent(
            resource=f"{wrapped.__module__}.{wrapped.__qualname__}",
            service=_get_ray_service_name(),
            component=config.ray.integration_name,
            distributed_context=_TraceContext._extract(kwargs[DD_RAY_TRACE_CTX]),
            use_active_context=tracer.context_provider.active() is not None,
            integration_config=config.ray,
            activate=True,
            method_args=args,
            method_kwargs=kwargs,
            is_remote_task=True,
        )
    ):
        # DD_RAY_TRACE_CTX is a transport-only kwarg used for propagation.
        # Remove it before invoking user code because runtime invocation validates
        # kwargs against the original task callable signature.
        kwargs.pop(DD_RAY_TRACE_CTX, None)
        result = wrapped(*args, **kwargs)
        return result


def traced_submit_task(wrapped, instance, args, kwargs):
    """Trace task submission, i.e the func.remote() call"""

    # Tracing doesn't work for cross lang yet.
    if instance._function.__module__ in RAY_TASK_MODULE_DENYLIST or instance._is_cross_language:
        return wrapped(*args, **kwargs)

    # Inject dd_trace_ctx args in the function being executed by ray
    # This is done under a lock as multiple task could be submit at the same time
    # and thus try to modify the signature as the same time
    with instance._inject_lock:
        if instance._function_signature is None:
            instance._function = _wrap_remote_function_execution(instance._function)
            instance._function.__signature__ = _inject_dd_trace_ctx_kwarg(instance._function)  # type: ignore[attr-defined]
            instance._function_signature = extract_signature(instance._function)

    # Check if the task has been instrumented so we can inject the context in kwargs
    inject_context = DD_RAY_TRACE_CTX in inspect.signature(instance._function).parameters

    if not config.ray.submission_spans:
        if inject_context:
            core.dispatch_event(RayContextInjectionEvent(kwargs=kwargs))
        return wrapped(*args, **kwargs)

    parent_context = tracer.current_trace_context() or _extract_tracing_context_from_env()
    with core.context_with_event(
        RaySubmissionEvent(
            component=config.ray.integration_name,
            service=_get_ray_service_name(),
            resource=f"{instance._function_name}.remote",
            integration_config=config.ray,
            method_args=kwargs.get("args"),
            method_kwargs=kwargs.get("kwargs"),
            is_task_submission=True,
            distributed_context=parent_context,
            use_active_context=parent_context is None,
        )
    ):
        if inject_context:
            core.dispatch_event(RayContextInjectionEvent(kwargs=kwargs))
        return wrapped(*args, **kwargs)
