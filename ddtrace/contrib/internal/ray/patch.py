from contextlib import contextmanager
from functools import wraps
import inspect
import os
import threading
import time
from typing import Any
from typing import Callable
from typing import List
from typing import Optional

import ray
from ray._private.inspect_util import is_class_method
from ray._private.inspect_util import is_function_or_method
from ray._private.inspect_util import is_static_method
import ray._private.worker
import ray.actor
import ray.dashboard.modules.job.job_manager
import ray.dashboard.modules.job.job_supervisor
import ray.exceptions
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace import tracer
from ddtrace._trace.span import Span
from ddtrace.constants import _DJM_ENABLED_KEY
from ddtrace.constants import _FILTER_KEPT_KEY
from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.propagation.http import _TraceContext
from ddtrace.settings._config import _get_config
from ddtrace.vendor.packaging.version import parse as parse_version

from .utils import _extract_tracing_context_from_env
from .utils import _inject_context_in_env
from .utils import _inject_context_in_kwargs
from .utils import _inject_dd_trace_ctx_kwarg


class _JobSpanManager:
    """Thread-safe manager for job spans that avoids closure serialization issues."""

    def __init__(self):
        self._spans = {}
        self._lock = threading.Lock()

    def add_span(self, submission_id: str, span: Span):
        with self._lock:
            self._spans[submission_id] = span

    def remove_span(self, submission_id: str) -> Optional[Span]:
        with self._lock:
            return self._spans.pop(submission_id, None)

    def get_span(self, submission_id: str) -> Optional[Span]:
        with self._lock:
            return self._spans.get(submission_id)


_job_span_manager = _JobSpanManager()


class RayTraceProcessor:
    def process_trace(self, trace: List[Span]) -> Optional[List[Span]]:
        if not trace:
            return trace

        processed_trace = []
        ray_spans_only = config.ray.ray_spans_only
        for span in trace:
            if span.get_tag("component") == "ray":
                span.set_metric(_DJM_ENABLED_KEY, 1)
                span.set_metric(_FILTER_KEPT_KEY, 1)
                span.set_metric(_SPAN_MEASURED_KEY, 1)
                span.set_metric(_SAMPLING_PRIORITY_KEY, 2)
                # Set the span type to either producer or consumer to match Ray's own OpenTelemetry implementation
                if span.span_type == SpanTypes.ML or span.span_type == SpanTypes.WORKER:
                    span.span_type = "consumer"
                else:
                    span.span_type = "producer"
                processed_trace.append(span)
            elif not ray_spans_only:
                with open("span_log.txt", "a") as log_file:
                    log_file.write(f"Span: {span}\n")
                processed_trace.append(span)

        return processed_trace


config._add(
    "ray",
    dict(
        _default_service=schematize_service_name("ray"),
        ray_spans_only=asbool(_get_config("DD_TRACE_RAY_SPANS_ONLY", default=True)),
    ),
)


def get_version() -> str:
    return parse_version(getattr(ray, "__version__", ""))


def _inject_tracing_into_function(function):
    """Inject trace context parameter into function signature"""

    def wrapped_function(*args, **kwargs):
        return _wrap_task_execution(function, *args, **kwargs)

    return wrapped_function


def _wrap_task_execution(wrapped, *args, **kwargs):
    """
    Wraps the actual execution of a Ray task to trace its performance.
    """
    if not tracer or "_dd_trace_ctx" not in kwargs:
        return wrapped(*args, **kwargs)

    # Extract context from parent span
    extracted_context = _TraceContext._extract(kwargs["_dd_trace_ctx"])
    kwargs.pop("_dd_trace_ctx")

    function_name = getattr(wrapped, "__name__", "unknown_function")
    function_module = getattr(wrapped, "__module__", "unknown_module")

    tracer.context_provider.activate(extracted_context)
    with tracer.trace("ray.task.execute", service=os.environ.get("_RAY_SUBMISSION_ID"), span_type=SpanTypes.ML) as span:
        span.set_tag_str("component", "ray")
        span.set_tag_str(SPAN_KIND, SpanKind.CONSUMER)
        span.resource = f"{function_module}.{function_name}"
        try:
            result = wrapped(*args, **kwargs)
            span.set_tag_str("ray.task.status", "success")
            return result
        except Exception as e:
            span.set_tag_str("ray.task.status", "error")
            raise e


def traced_remote_init(wrapped, instance, args, kwargs):
    if not tracer:
        return wrapped(*args, **kwargs)

    result = wrapped(*args, **kwargs)
    with instance._inject_lock:
        if not hasattr(instance, "_tracing_injected"):
            instance._function = _inject_tracing_into_function(instance._function)
            instance._function.__signature__ = _inject_dd_trace_ctx_kwarg(instance._function)
            instance._tracing_injected = True

    return result


def traced_submit_task(wrapped, instance, args, kwargs):
    if not tracer:
        return wrapped(*args, **kwargs)

    if tracer.current_span() is None:
        tracer.context_provider.activate(_extract_tracing_context_from_env())

    with tracer.trace("ray.task.submit", service=os.environ.get("_RAY_SUBMISSION_ID"), span_type=SpanTypes.ML) as span:
        span.set_tag_str("component", "ray")
        span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)
        try:
            _inject_context_in_kwargs(span.context, kwargs)

            resp = wrapped(*args, **kwargs)

            span.set_tag_str("ray.task.submit_status", "success")
            return resp
        except Exception as e:
            span.set_tag_str("ray.task.submit_status", "error")
            raise e


def traced_submit_job(wrapped, instance, args, kwargs):
    if not tracer:
        return wrapped(*args, **kwargs)

    submission_id = kwargs["submission_id"]
    job_span = tracer.start_span("ray.job", service=submission_id, span_type=SpanTypes.ML)
    job_span.set_tag_str("component", "ray")
    _job_span_manager.add_span(submission_id, job_span)

    # Set global span
    tracer.context_provider.activate(job_span)
    try:
        with tracer.trace("ray.job.submit", service=submission_id, span_type=SpanTypes.ML) as submit_span:
            submit_span.set_tag_str("component", "ray")
            submit_span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)

            # Inject the context of the job so that ray.job.run is its child
            env_vars = kwargs.setdefault("runtime_env", {}).setdefault("env_vars", {})
            _TraceContext._inject(job_span.context, env_vars)
            env_vars["_RAY_SUBMISSION_ID"] = kwargs.get("submission_id", "")

            try:
                resp = wrapped(*args, **kwargs)
                submit_span.set_tag_str("ray.job.submit_status", "success")
                return resp
            except Exception:
                submit_span.set_tag_str("ray.job.submit_status", "error")
                raise
    except Exception as e:
        job_span.set_tag_str("ray.job.status", "error")
        job_span.error = 1
        job_span.set_exc_info(type(e), e, e.__traceback__)
        _job_span_manager.remove_span(job_span)
        job_span.finish()
        raise e


def traced_actor_method_call(wrapped, instance, args, kwargs):
    if not tracer:
        return wrapped(*args, **kwargs)

    actor_name = instance._ray_actor_creation_function_descriptor.class_name
    method_name = args[0]
    # if _dd_trace_ctx was not injected, we do not want to trace this function
    if not any(p.name == "_dd_trace_ctx" for p in instance._ray_method_signatures[method_name]):
        return wrapped(*args, **kwargs)

    if tracer.current_span() is None:
        tracer.context_provider.activate(_extract_tracing_context_from_env())

    with tracer.trace(
        "ray.actor.method.call", service=os.environ.get("_RAY_SUBMISSION_ID"), span_type=SpanTypes.ML
    ) as span:
        span.set_tag_str("component", "ray")
        span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)
        span.resource = f"{actor_name}.{method_name}.remote()"

        try:
            _inject_context_in_kwargs(span.context, kwargs)
            return wrapped(*args, **kwargs)
        except Exception as e:
            raise e


def flush_worker_spans(wrapped, instance, args, kwargs):
    # Ensure the tracer has the time to send spans before
    # before the worker is killed
    if not tracer:
        return wrapped(*args, **kwargs)

    time.sleep(0.5)
    return wrapped(*args, **kwargs)


def job_supervisor_run_wrapper(method: Callable[..., Any]) -> Any:
    async def _traced_run_method(self: Any, *_args: Any, _dd_trace_ctx=None, **_kwargs: Any) -> Any:
        from ddtrace import tracer as dd_tracer
        from ddtrace.ext import SpanTypes

        if not dd_tracer:
            return await method(self, *_args, **_kwargs)

        dd_tracer.context_provider.activate(_extract_tracing_context_from_env())

        method_name = f"{self.__class__.__name__}.{method.__name__}"
        with dd_tracer.trace(
            "ray.job.run", service=os.environ.get("_RAY_SUBMISSION_ID"), span_type=SpanTypes.ML
        ) as span:
            span.set_tag_str("component", "ray")
            span.set_tag_str(SPAN_KIND, SpanKind.CONSUMER)
            span.resource = method_name

            _inject_context_in_env(span.context)

            try:
                await method(self, *_args, **_kwargs)
            except ray.exceptions.AsyncioActorExit:
                pass  # only if job failed ?
            except Exception as e:
                raise e

    return _traced_run_method


@contextmanager
def _trace_scope(self: Any, method: Callable[..., Any], dd_trace_ctx):
    if tracer.current_span() is None:
        context = _TraceContext._extract(dd_trace_ctx) if dd_trace_ctx else _extract_tracing_context_from_env()
        tracer.context_provider.activate(context)

    method_name = f"{self.__class__.__name__}.{method.__name__}"
    with tracer.trace(
        "ray.actor.method",
        service=os.environ.get("_RAY_SUBMISSION_ID"),
        span_type=SpanTypes.ML,
    ) as span:
        span.set_tag_str("component", "ray")
        span.set_tag_str(SPAN_KIND, SpanKind.CONSUMER)
        span.resource = method_name

        yield span


def _create_span_wrapper(method: Callable[..., Any]) -> Any:
    def _traced_method(self: Any, *args: Any, _dd_trace_ctx=None, **kwargs: Any) -> Any:
        from ddtrace import tracer

        if not tracer:
            return method(self, *args, **kwargs)

        with _trace_scope(self, method, _dd_trace_ctx, **kwargs):
            return method(self, *args, **kwargs)

    return _traced_method


def _create_async_span_wrapper(method: Callable[..., Any]) -> Any:
    async def _traced_async_method(self: Any, *args: Any, _dd_trace_ctx=None, **kwargs: Any) -> Any:
        from ddtrace import tracer

        if not tracer:
            return await method(self, *args, **kwargs)

        with _trace_scope(self, method, _dd_trace_ctx, **kwargs):
            return await method(self, *args, **kwargs)

    return _traced_async_method


def inject_tracing_into_actor_class(wrapped, instance, args, kwargs):
    if not tracer:
        return wrapped(*args, **kwargs)

    cls = wrapped(*args, **kwargs)
    module_name = str(cls.__module__)
    class_name = str(cls.__name__)

    # Skip tracing for certain ray modules
    if module_name.startswith("ray.dag") or module_name.startswith("ray.experimental"):
        return cls

    # Determine if the class is a JobSupervisor
    is_job_supervisor = f"{module_name}.{class_name}" == "ray.dashboard.modules.job.job_supervisor.JobSupervisor"
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
            continue

        method.__signature__ = _inject_dd_trace_ctx_kwarg(method)

        # Special handling for the run method in JobSupervisor
        if is_job_supervisor and name == "run" and inspect.iscoroutinefunction(method):
            wrapped_method = wraps(method)(job_supervisor_run_wrapper(method))
        else:
            if inspect.iscoroutinefunction(method):
                wrapped_method = wraps(method)(_create_async_span_wrapper(method))
            else:
                wrapped_method = wraps(method)(_create_span_wrapper(method))

        setattr(cls, name, wrapped_method)

    return cls


async def traced_end_job(wrapped, instance, args, kwargs):
    if not tracer:
        return await wrapped(*args, **kwargs)

    result = await wrapped(*args, **kwargs)

    # At this stage, the job is finished
    job_id = args[0]
    job_span = _job_span_manager.get_span(job_id)
    if job_span is None:
        return result

    job_info = await instance._job_info_client.get_info(job_id)
    job_span.set_tag_str("ray.job.status", job_info.status)
    job_span.set_tag_str("ray.job.message", job_info.message)

    # Set error tags if job failed
    if str(job_info.status) == "FAILED":
        job_span.error = 1
        job_span.set_tag_str("error.message", job_info.message)
    job_span.finish()

    return result


def patch():
    if getattr(ray, "_datadog_patch", False):
        return

    ray._datadog_patch = True

    tracer._span_aggregator.user_processors.append(RayTraceProcessor())

    _w(ray.remote_function, "RemoteFunction.__init__", traced_remote_init)
    _w(ray.remote_function, "RemoteFunction._remote", traced_submit_task)

    _w(ray.dashboard.modules.job.job_manager.JobManager, "submit_job", traced_submit_job)
    _w(ray.dashboard.modules.job.job_manager.JobManager, "_monitor_job_internal", traced_end_job)

    _w(ray.actor, "_modify_class", inject_tracing_into_actor_class)
    _w(ray.actor.ActorHandle, "_actor_method_call", traced_actor_method_call)

    _w(ray._private.worker, "disconnect", flush_worker_spans)


def unpatch():
    if not getattr(ray, "_datadog_patch", False):
        return

    ray._datadog_patch = False

    tracer._span_aggregator.user_processors = [
        p for p in tracer._span_aggregator.user_processors if not isinstance(p, RayTraceProcessor)
    ]

    _u(ray.remote_function, "RemoteFunction.__init__", traced_remote_init)
    _u(ray.remote_function, "RemoteFunction._remote", traced_submit_task)

    _u(ray.dashboard.modules.job.job_manager.JobManager, "submit_job", traced_submit_job)
    _u(ray.dashboard.modules.job.job_manager.JobManager, "_monitor_job_internal", traced_end_job)

    _u(ray.actor, "_modify_class", inject_tracing_into_actor_class)
    _u(ray.actor.ActorHandle, "_actor_method_call", traced_actor_method_call)

    _u(ray._private.worker, "disconnect", flush_worker_spans)
