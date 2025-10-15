from contextlib import contextmanager
from functools import wraps
import inspect
import os
import sys
from typing import Any
from typing import Callable
from typing import Dict

import ray
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace import tracer
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.telemetry import get_config as _get_config
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.propagation.http import _TraceContext

from .constants import DD_RAY_TRACE_CTX
from .constants import DEFAULT_JOB_NAME
from .constants import RAY_ACTOR_METHOD_ARGS
from .constants import RAY_ACTOR_METHOD_KWARGS
from .constants import RAY_ENTRYPOINT
from .constants import RAY_GET_VALUE_SIZE_BYTES
from .constants import RAY_JOB_NAME
from .constants import RAY_JOB_STATUS
from .constants import RAY_JOB_SUBMIT_STATUS
from .constants import RAY_PUT_VALUE_SIZE_BYTES
from .constants import RAY_PUT_VALUE_TYPE
from .constants import RAY_STATUS_ERROR
from .constants import RAY_STATUS_FAILED
from .constants import RAY_STATUS_SUCCESS
from .constants import RAY_SUBMISSION_ID
from .constants import RAY_SUBMISSION_ID_TAG
from .constants import RAY_TASK_ARGS
from .constants import RAY_TASK_KWARGS
from .constants import RAY_TASK_STATUS
from .constants import RAY_TASK_SUBMIT_STATUS
from .constants import RAY_WAIT_FETCH_LOCAL
from .constants import RAY_WAIT_NUM_RETURNS
from .constants import RAY_WAIT_TIMEOUT
from .span_manager import long_running_ray_span
from .span_manager import start_long_running_job
from .span_manager import stop_long_running_job
from .utils import ENTRY_POINT_REGEX
from .utils import _extract_tracing_context_from_env
from .utils import _inject_context_in_env
from .utils import _inject_context_in_kwargs
from .utils import _inject_dd_trace_ctx_kwarg
from .utils import _inject_ray_span_tags_and_metrics
from .utils import extract_signature
from .utils import flatten_metadata_dict
from .utils import get_dd_job_name_from_entrypoint
from .utils import redact_paths
from .utils import set_tag_or_truncate


log = get_logger(__name__)

RAY_SERVICE_NAME = os.environ.get(RAY_JOB_NAME)

# Ray modules that should be excluded from tracing
RAY_MODULE_DENYLIST = {
    "ray.dag",
    "ray.experimental",
}


config._add(
    "ray",
    dict(
        _default_service=schematize_service_name("ray"),
        use_entrypoint_as_service_name=asbool(os.getenv("DD_TRACE_RAY_USE_ENTRYPOINT_AS_SERVICE_NAME", default=False)),
        redact_entrypoint_paths=asbool(os.getenv("DD_TRACE_RAY_REDACT_ENTRYPOINT_PATHS", default=True)),
        trace_core_api=_get_config("DD_TRACE_RAY_CORE_API", default=False, modifier=asbool),
        trace_args_kwargs=_get_config("DD_TRACE_RAY_ARGS_KWARGS", default=False, modifier=asbool),
    ),
)


def _supported_versions() -> Dict[str, str]:
    return {"ray": ">=2.46.0"}


def get_version() -> str:
    return str(getattr(ray, "__version__", ""))


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

    # Extract context from parent span
    extracted_context = _TraceContext._extract(kwargs[DD_RAY_TRACE_CTX])
    kwargs.pop(DD_RAY_TRACE_CTX)

    with long_running_ray_span(
        "task.execute",
        resource=f"{wrapped.__module__}.{wrapped.__qualname__}",
        service=RAY_SERVICE_NAME,
        span_type=SpanTypes.RAY,
        child_of=extracted_context,
        activate=True,
    ) as task_execute_span:
        try:
            if config.ray.trace_args_kwargs:
                set_tag_or_truncate(task_execute_span, RAY_TASK_ARGS, args)
                set_tag_or_truncate(task_execute_span, RAY_TASK_KWARGS, kwargs)

            result = wrapped(*args, **kwargs)

            task_execute_span.set_tag_str(RAY_TASK_STATUS, RAY_STATUS_SUCCESS)
            return result
        except BaseException as e:
            log.debug(
                "Ray task %s execution failed: %s", f"{wrapped.__module__}.{wrapped.__qualname__}", e, exc_info=True
            )
            task_execute_span.set_tag_str(RAY_TASK_STATUS, RAY_STATUS_ERROR)
            raise


def traced_submit_task(wrapped, instance, args, kwargs):
    """Trace task submission, i.e the func.remote() call"""
    if tracer.current_span() is None:
        log.debug(
            "No active span found in %s.remote(), activating trace context from environment", instance._function_name
        )
        tracer.context_provider.activate(_extract_tracing_context_from_env())

    # Inject dd_trace_ctx args in the function being executed by ray
    # This is done under a lock as multiple task could be submit at the same time
    # and thus try to modify the signature as the same time
    with instance._inject_lock:
        if instance._function_signature is None:
            instance._function = _wrap_remote_function_execution(instance._function)
            instance._function.__signature__ = _inject_dd_trace_ctx_kwarg(instance._function)
            instance._function_signature = extract_signature(instance._function)

    with tracer.trace(
        "task.submit",
        resource=f"{instance._function_name}.remote",
        service=RAY_SERVICE_NAME,
        span_type=SpanTypes.RAY,
    ) as span:
        span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)
        _inject_ray_span_tags_and_metrics(span)

        try:
            if config.ray.trace_args_kwargs:
                set_tag_or_truncate(span, RAY_TASK_ARGS, kwargs.get("args", {}))
                set_tag_or_truncate(span, RAY_TASK_KWARGS, kwargs.get("kwargs", {}))
            _inject_context_in_kwargs(span.context, kwargs)

            resp = wrapped(*args, **kwargs)

            span.set_tag_str(RAY_TASK_SUBMIT_STATUS, RAY_STATUS_SUCCESS)
            return resp
        except BaseException as e:
            log.debug("Failed to submit Ray task %s : %s", f"{instance._function_name}.remote()", e, exc_info=True)
            span.set_tag_str(RAY_TASK_SUBMIT_STATUS, RAY_STATUS_ERROR)
            raise e


def traced_submit_job(wrapped, instance, args, kwargs):
    """Trace job submission. This function is also responsible
    of creating the root span.
    It will also inject _RAY_SUBMISSION_ID and _RAY_JOB_NAME
    in the env variable as some spans will not have access to them
    trough ray_ctx
    """
    from ray.dashboard.modules.job.job_manager import generate_job_id

    # Three ways of specifying the job name, in order of precedence:
    # 1. Metadata JSON: ray job submit --metadata_json '{"job_name": "train.cool.model"}' train.py
    # 2. Special submission ID format: ray job submit --submission_id "job:train.cool.model,run:38" train.py
    # 3. Ray entrypoint: ray job submit train_cool_model.py
    submission_id = kwargs.get("submission_id") or generate_job_id()
    kwargs["submission_id"] = submission_id
    entrypoint = kwargs.get("entrypoint", "")
    if entrypoint and config.ray.redact_entrypoint_paths:
        entrypoint = redact_paths(entrypoint)
    job_name = config.service or kwargs.get("metadata", {}).get("job_name", "")

    if not job_name:
        if config.ray.use_entrypoint_as_service_name:
            job_name = get_dd_job_name_from_entrypoint(entrypoint) or DEFAULT_JOB_NAME
        else:
            job_name = DEFAULT_JOB_NAME

    job_span = tracer.start_span("ray.job", service=job_name or DEFAULT_JOB_NAME, span_type=SpanTypes.RAY)
    try:
        # Root span creation
        _inject_ray_span_tags_and_metrics(job_span)
        job_span.set_tag_str(RAY_SUBMISSION_ID_TAG, submission_id)
        if entrypoint:
            job_span.set_tag_str(RAY_ENTRYPOINT, entrypoint)

        metadata = kwargs.get("metadata", {})
        dot_paths = flatten_metadata_dict(metadata)
        for k, v in dot_paths.items():
            set_tag_or_truncate(job_span, k, v)

        tracer.context_provider.activate(job_span)
        start_long_running_job(job_span)

        with tracer.trace(
            "ray.job.submit", service=job_name or DEFAULT_JOB_NAME, span_type=SpanTypes.RAY
        ) as submit_span:
            _inject_ray_span_tags_and_metrics(submit_span)
            submit_span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)
            submit_span.set_tag_str(RAY_SUBMISSION_ID_TAG, submission_id)

            # Inject the context of the job so that ray.job.run is its child
            runtime_env = kwargs.get("runtime_env") or {}
            kwargs["runtime_env"] = runtime_env
            env_vars = runtime_env.get("env_vars") or {}
            runtime_env["env_vars"] = env_vars

            _TraceContext._inject(job_span.context, env_vars)
            env_vars[RAY_SUBMISSION_ID] = submission_id
            if job_name:
                env_vars[RAY_JOB_NAME] = job_name

            try:
                resp = wrapped(*args, **kwargs)
                submit_span.set_tag_str(RAY_JOB_SUBMIT_STATUS, RAY_STATUS_SUCCESS)
                return resp
            except BaseException as e:
                log.debug("Failed to submit Ray Job %s : %s", job_name, e, exc_info=True)
                submit_span.set_tag_str(RAY_JOB_SUBMIT_STATUS, RAY_STATUS_ERROR)
                raise
    except BaseException as e:
        job_span.set_tag_str(RAY_JOB_STATUS, RAY_STATUS_ERROR)
        job_span.error = 1
        job_span.set_exc_info(type(e), e, e.__traceback__)
        stop_long_running_job(submission_id)
        raise e


def traced_actor_method_call(wrapped, instance, args, kwargs):
    """Trace actor method submission, i.e the Actor.func.remote()
    call
    """
    actor_name = instance._ray_actor_creation_function_descriptor.class_name
    method_name = get_argument_value(args, kwargs, 0, "method_name")

    # if _dd_trace_ctx was not injected in the param of the function, it means
    # we do not want to trace this function, for example: JobSupervisor.ping
    if not any(p.name == DD_RAY_TRACE_CTX for p in instance._ray_method_signatures[method_name]):
        return wrapped(*args, **kwargs)

    if tracer.current_span() is None:
        log.debug(
            "No active span found in %s.%s.remote(), activating trace context from environment", actor_name, method_name
        )
        tracer.context_provider.activate(_extract_tracing_context_from_env())

    with tracer.trace(
        "actor_method.submit",
        service=RAY_SERVICE_NAME,
        span_type=SpanTypes.RAY,
        resource=f"{actor_name}.{method_name}.remote",
    ) as span:
        span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)
        if config.ray.trace_args_kwargs:
            set_tag_or_truncate(span, RAY_ACTOR_METHOD_ARGS, get_argument_value(args, kwargs, 0, "args"))
            set_tag_or_truncate(span, RAY_ACTOR_METHOD_KWARGS, get_argument_value(args, kwargs, 1, "kwargs"))
        _inject_ray_span_tags_and_metrics(span)

        _inject_context_in_kwargs(span.context, kwargs)
        return wrapped(*args, **kwargs)


def traced_get(wrapped, instance, args, kwargs):
    """
    Trace the calls of ray.get
    """
    if not config.ray.trace_core_api:
        return wrapped(*args, **kwargs)

    if tracer.current_span() is None:
        tracer.context_provider.activate(_extract_tracing_context_from_env())

    with long_running_ray_span(
        "ray.get",
        service=RAY_SERVICE_NAME or DEFAULT_JOB_NAME,
        span_type=SpanTypes.RAY,
        child_of=tracer.context_provider.active(),
        activate=True,
    ) as span:
        span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)
        timeout = kwargs.get("timeout")
        if timeout is not None:
            span.set_tag_str("ray.get.timeout_s", str(timeout))
        _inject_ray_span_tags_and_metrics(span)
        get_value = get_argument_value(args, kwargs, 0, "object_refs")
        span.set_tag_str(RAY_GET_VALUE_SIZE_BYTES, str(sys.getsizeof(get_value)))
        return wrapped(*args, **kwargs)


def traced_put(wrapped, instance, args, kwargs):
    """
    Trace the calls of ray.put
    """
    if not config.ray.trace_core_api:
        return wrapped(*args, **kwargs)

    if tracer.current_span() is None:
        tracer.context_provider.activate(_extract_tracing_context_from_env())

    with tracer.trace("ray.put", service=RAY_SERVICE_NAME or DEFAULT_JOB_NAME, span_type=SpanTypes.RAY) as span:
        span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)
        _inject_ray_span_tags_and_metrics(span)

        put_value = get_argument_value(args, kwargs, 0, "value")
        span.set_tag_str(RAY_PUT_VALUE_TYPE, str(type(put_value).__name__))
        span.set_tag_str(RAY_PUT_VALUE_SIZE_BYTES, str(sys.getsizeof(put_value)))

        return wrapped(*args, **kwargs)


def traced_wait(wrapped, instance, args, kwargs):
    """
    Trace the calls of ray.wait
    """
    if not config.ray.trace_core_api:
        return wrapped(*args, **kwargs)

    if tracer.current_span() is None:
        log.debug("No active span found in ray.wait(), activating trace context from environment")
        tracer.context_provider.activate(_extract_tracing_context_from_env())

    with long_running_ray_span(
        "ray.wait",
        service=RAY_SERVICE_NAME,
        span_type=SpanTypes.RAY,
        child_of=tracer.context_provider.active(),
        activate=True,
    ) as span:
        span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)
        _inject_ray_span_tags_and_metrics(span)

        timeout = kwargs.get("timeout")
        num_returns = kwargs.get("num_returns")
        fetch_local = kwargs.get("fetch_local")
        if timeout is not None:
            span.set_tag_str(RAY_WAIT_TIMEOUT, str(timeout))
        if num_returns is not None:
            span.set_tag_str(RAY_WAIT_NUM_RETURNS, str(num_returns))
        if fetch_local is not None:
            span.set_tag_str(RAY_WAIT_FETCH_LOCAL, str(fetch_local))
        return wrapped(*args, **kwargs)


def _job_supervisor_run_wrapper(method: Callable[..., Any]) -> Any:
    async def _traced_run_method(self: Any, *args: Any, _dd_ray_trace_ctx, **kwargs: Any) -> Any:
        import ray.exceptions

        from ddtrace.ext import SpanTypes

        context = _TraceContext._extract(_dd_ray_trace_ctx)
        submission_id = os.environ.get(RAY_SUBMISSION_ID)

        with long_running_ray_span(
            "actor_method.execute",
            resource=f"{self.__class__.__name__}.{method.__name__}",
            service=os.environ.get(RAY_JOB_NAME, DEFAULT_JOB_NAME),
            span_type=SpanTypes.RAY,
            child_of=context,
            activate=True,
        ) as supervisor_run_span:
            _inject_context_in_env(supervisor_run_span.context)

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
        from ddtrace import tracer
        from ddtrace.ext import SpanTypes

        script_name_match = ENTRY_POINT_REGEX.search(self._entrypoint)
        if script_name_match:
            entrypoint_name = f"{script_name_match.group(1)}.py"
        else:
            entrypoint_name = os.path.basename(self._entrypoint)

        if tracer.current_span() is None:
            log.debug("No active span found in exec %s, activating trace context from environment", entrypoint_name)
            tracer.context_provider.activate(_extract_tracing_context_from_env())

        with tracer.trace(
            "exec entrypoint",
            resource=f"exec {entrypoint_name}",
            service=os.environ.get(RAY_JOB_NAME, DEFAULT_JOB_NAME),
            span_type=SpanTypes.RAY,
        ) as span:
            span.set_tag_str(SPAN_KIND, SpanKind.CONSUMER)
            _inject_ray_span_tags_and_metrics(span)

            return method(self, *args, **kwargs)

    return _traced_exec_entrypoint_method


@contextmanager
def _trace_actor_method(self: Any, method: Callable[..., Any], dd_trace_ctx, *args, **kwargs):
    context = tracer.context_provider.active()
    if context is None:
        context = _TraceContext._extract(dd_trace_ctx)

    with long_running_ray_span(
        "actor_method.execute",
        resource=f"{self.__class__.__name__}.{method.__name__}",
        service=RAY_SERVICE_NAME,
        span_type=SpanTypes.RAY,
        child_of=context,
        activate=True,
    ) as actor_execute_span:
        if config.ray.trace_args_kwargs:
            set_tag_or_truncate(actor_execute_span, RAY_ACTOR_METHOD_ARGS, args)
            set_tag_or_truncate(actor_execute_span, RAY_ACTOR_METHOD_KWARGS, kwargs)

        yield actor_execute_span


def _inject_tracing_actor_method(method: Callable[..., Any]) -> Any:
    def _traced_method(self: Any, *args: Any, _dd_ray_trace_ctx=None, **kwargs: Any) -> Any:
        from ddtrace import tracer

        if _dd_ray_trace_ctx is None and tracer.current_span() is None:
            return method(self, *args, **kwargs)

        with _trace_actor_method(self, method, _dd_ray_trace_ctx, *args, **kwargs):
            return method(self, *args, **kwargs)

    return _traced_method


def _inject_tracing_async_actor_method(method: Callable[..., Any]) -> Any:
    async def _traced_async_method(self: Any, *args: Any, _dd_ray_trace_ctx=None, **kwargs: Any) -> Any:
        from ddtrace import tracer

        if _dd_ray_trace_ctx is None and tracer.current_span() is None:
            return await method(self, *args, **kwargs)

        with _trace_actor_method(self, method, _dd_ray_trace_ctx, *args, **kwargs):
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
    if any(module_name.startswith(denied_module) for denied_module in RAY_MODULE_DENYLIST):
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


async def traced_end_job(wrapped, instance, args, kwargs):
    result = await wrapped(*args, **kwargs)

    job_id = get_argument_value(args, kwargs, 0, "job_id")
    job_info = await instance._job_info_client.get_info(job_id)
    stop_long_running_job(job_id, job_info)

    return result


def patch():
    if getattr(ray, "_datadog_patch", False):
        return

    ray._datadog_patch = True

    @ModuleWatchdog.after_module_imported("ray.actor")
    def _(m):
        _w(m.ActorHandle, "_actor_method_call", traced_actor_method_call)
        _w(m, "_modify_class", inject_tracing_into_actor_class)

    @ModuleWatchdog.after_module_imported("ray.dashboard.modules.job.job_manager")
    def _(m):
        _w(m.JobManager, "submit_job", traced_submit_job)
        _w(m.JobManager, "_monitor_job_internal", traced_end_job)

    @ModuleWatchdog.after_module_imported("ray.remote_function")
    def _(m):
        _w(m.RemoteFunction, "_remote", traced_submit_task)

    _w(ray, "get", traced_get)
    _w(ray, "wait", traced_wait)
    _w(ray, "put", traced_put)


def unpatch():
    if not getattr(ray, "_datadog_patch", False):
        return

    _u(ray.remote_function.RemoteFunction, "_remote")

    _u(ray.dashboard.modules.job.job_manager.JobManager, "submit_job")
    _u(ray.dashboard.modules.job.job_manager.JobManager, "_monitor_job_internal")

    _u(ray.actor, "_modify_class")
    _u(ray.actor.ActorHandle, "_actor_method_call")

    _u(ray, "get")
    _u(ray, "wait")
    _u(ray, "put")

    ray._datadog_patch = False
