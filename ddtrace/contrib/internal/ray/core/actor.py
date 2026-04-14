from contextlib import contextmanager
from functools import wraps
import inspect
import os
from typing import Any
from typing import Callable

from ddtrace.contrib._events.ray import RayContextInjectionEvent
from ddtrace.contrib._events.ray import RayExecutionEvent
from ddtrace.contrib._events.ray import RaySubmissionEvent
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.utils import get_argument_value
from ddtrace.propagation.http import _TraceContext

from ..constants import DD_RAY_TRACE_CTX
from ..constants import RAY_STATUS_FAILED
from ..constants import RAY_SUBMISSION_ID
from .utils import ENTRY_POINT_REGEX
from .utils import _extract_tracing_context_from_env
from .utils import _get_ray_service_name
from .utils import _inject_context_in_env
from .utils import _inject_dd_trace_ctx_kwarg


log = get_logger(__name__)


RAY_ACTOR_MODULE_DENYLIST = {
    "ray.data._internal",
    "ray.experimental",
    "ray.data._internal",
}


def _get_active_context():
    # Avoid capturing global tracer object in serialized actor wrappers.
    from ddtrace.trace import tracer

    return tracer.context_provider.active()


def _get_ray_integration_config():
    # Avoid capturing global config object in serialized actor wrappers.
    from ddtrace import config

    return config.ray


def traced_actor_method_submission(wrapped, instance, args, kwargs):
    """Trace actor method submission, i.e the Actor.func.remote()
    call
    """
    actor_name = instance._ray_actor_creation_function_descriptor.class_name
    method_name = get_argument_value(args, kwargs, 0, "method_name")

    # if _dd_trace_ctx was not injected in the param of the function, it means
    # we do not want to trace this function, for example: JobSupervisor.ping
    if not any(p.name == DD_RAY_TRACE_CTX for p in instance._ray_method_signatures[method_name]):
        return wrapped(*args, **kwargs)

    ray_config = _get_ray_integration_config()
    if not ray_config.submission_spans:
        core.dispatch_event(RayContextInjectionEvent(kwargs=kwargs))
        return wrapped(*args, **kwargs)

    parent_context = _get_active_context() or _extract_tracing_context_from_env()

    with core.context_with_event(
        RaySubmissionEvent(
            component=ray_config.integration_name,
            integration_config=ray_config,
            service=_get_ray_service_name(),
            resource=f"{actor_name}.{method_name}.remote",
            method_args=get_argument_value(args, kwargs, 1, "args"),
            method_kwargs=get_argument_value(args, kwargs, 2, "kwargs"),
            is_actor_method=True,
            is_task_submission=False,
            distributed_context=parent_context,
            use_active_context=parent_context is None,
        )
    ):
        core.dispatch_event(RayContextInjectionEvent(kwargs=kwargs))
        return wrapped(*args, **kwargs)


@contextmanager
def _trace_actor_method_execution(self: Any, method: Callable[..., Any], dd_trace_ctx, *args, **kwargs):
    ray_config = _get_ray_integration_config()

    active_context = _get_active_context()
    context = None
    if active_context is None and dd_trace_ctx is not None:
        # Actor methods may execute in a worker process with no active tracer context.
        # In that case, recover distributed context from the injected Ray kwarg.
        context = _TraceContext._extract(dd_trace_ctx)
    if context is None:
        context = _extract_tracing_context_from_env()

    with core.context_with_event(
        RayExecutionEvent(
            resource=f"{self.__class__.__name__}.{method.__name__}",
            service=_get_ray_service_name(),
            component=ray_config.integration_name,
            distributed_context=context,
            use_active_context=active_context is not None,
            integration_config=ray_config,
            activate=True,
            method_args=args,
            method_kwargs=kwargs,
            is_actor_method=True,
        )
    ) as ctx:
        yield ctx.span


def _job_supervisor_run_wrapper(method: Callable[..., Any]) -> Any:
    async def _traced_run_method(self: Any, *args: Any, _dd_ray_trace_ctx=None, **kwargs: Any) -> Any:
        import ray.exceptions

        submission_id = env.get(RAY_SUBMISSION_ID)

        with _trace_actor_method_execution(self, method, _dd_ray_trace_ctx, *args, **kwargs) as span:
            _inject_context_in_env(span.context)

            try:
                await method(self, *args, **kwargs)
            except ray.exceptions.AsyncioActorExit as e:
                # if the job succeeded we remove from the span
                # the error used to exit the actor
                job_info = await self._job_info_client.get_info(submission_id)

                if str(job_info.status) == RAY_STATUS_FAILED:
                    raise e

    return _traced_run_method


def _exec_entrypoint_wrapper(method: Callable[..., Any]) -> Any:
    def _traced_exec_entrypoint_method(self: Any, *args: Any, _dd_ray_trace_ctx=None, **kwargs: Any) -> Any:
        ray_config = _get_ray_integration_config()
        script_name_match = ENTRY_POINT_REGEX.search(self._entrypoint)
        if script_name_match:
            entrypoint_name = f"{script_name_match.group(1)}.py"
        else:
            entrypoint_name = os.path.basename(self._entrypoint)

        active_context = _get_active_context()
        context = None
        if active_context is None:
            log.debug("No active span found in exec %s, activating trace context from environment", entrypoint_name)
            context = _extract_tracing_context_from_env()

        with core.context_with_event(
            RayExecutionEvent(
                resource=f"exec {entrypoint_name}",
                service=_get_ray_service_name(),
                component=ray_config.integration_name,
                distributed_context=context,
                use_active_context=active_context is not None,
                integration_config=ray_config,
                activate=True,
                method_args=args,
                method_kwargs=kwargs,
                is_actor_method=True,
            )
        ):
            return method(self, *args, **kwargs)

    return _traced_exec_entrypoint_method


# --------------------------------------------------------------------------- #
#                           Injection utilities
# --------------------------------------------------------------------------- #
def _inject_tracing_actor_method(method: Callable[..., Any]) -> Any:
    def _traced_method(self: Any, *args: Any, _dd_ray_trace_ctx=None, **kwargs: Any) -> Any:
        from ddtrace import tracer

        if _dd_ray_trace_ctx is None and tracer.current_span() is None:
            return method(self, *args, **kwargs)

        with _trace_actor_method_execution(self, method, _dd_ray_trace_ctx, *args, **kwargs):
            return method(self, *args, **kwargs)

    return _traced_method


def _inject_tracing_async_actor_method(method: Callable[..., Any]) -> Any:
    async def _traced_async_method(self: Any, *args: Any, _dd_ray_trace_ctx=None, **kwargs: Any) -> Any:
        from ddtrace import tracer

        if _dd_ray_trace_ctx is None and tracer.current_span() is None:
            return await method(self, *args, **kwargs)

        with _trace_actor_method_execution(self, method, _dd_ray_trace_ctx, *args, **kwargs):
            return await method(self, *args, **kwargs)

    return _traced_async_method


def inject_tracing_into_actor_class(wrapped, instance, args, kwargs):
    from ray._private.inspect_util import is_class_method
    from ray._private.inspect_util import is_function_or_method
    from ray._private.inspect_util import is_static_method

    cls = wrapped(*args, **kwargs)
    module_name = str(cls.__module__)
    class_name = str(cls.__name__)

    # Skip tracing for certain ray modules
    if any(module_name.startswith(denied_module) for denied_module in RAY_ACTOR_MODULE_DENYLIST):
        return cls

    # Actor beginning with _ are considered internal and will not be traced
    if class_name.startswith("_"):
        return cls

    # Determine if the class is a JobSupervisor
    is_job_supervisor = f"{module_name}.{class_name}" == "ray.dashboard.modules.job.job_supervisor.JobSupervisor"
    # We do not want to instrument ping and polling to remove noise
    methods_to_ignore = {"ping", "_polling"} if is_job_supervisor else set()

    methods = inspect.getmembers(cls, is_function_or_method)
    for name, method in methods:
        if name in methods_to_ignore:
            continue

        if (
            is_static_method(cls, name)
            or is_class_method(method)
            or inspect.isgeneratorfunction(method)
            or inspect.isasyncgenfunction(method)
            or name == "__del__"
        ):
            log.debug("Skipping method %s.%s (unsupported method type)", class_name, name)
            continue

        # Inject a transport-only kwarg into actor method signatures so the submit side
        # can propagate distributed trace context to execution workers.
        method.__signature__ = _inject_dd_trace_ctx_kwarg(method)

        # Special handling for the run method in JobSupervisor
        if is_job_supervisor and name == "run":
            wrapped_method = wraps(method)(_job_supervisor_run_wrapper(method))
        elif is_job_supervisor and name == "_exec_entrypoint":
            wrapped_method = wraps(method)(_exec_entrypoint_wrapper(method))
        else:
            if inspect.iscoroutinefunction(method):
                wrapped_method = wraps(method)(_inject_tracing_async_actor_method(method))
            else:
                wrapped_method = wraps(method)(_inject_tracing_actor_method(method))

        setattr(cls, name, wrapped_method)
    return cls
