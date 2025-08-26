from functools import wraps
import inspect
import json
import os
import time
from typing import Any
from typing import Callable
from typing import List
from typing import Optional
from inspect import Parameter

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
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.propagation.http import _TraceContext
from ddtrace.vendor.packaging.version import parse as parse_version
import ray
from ray._private.inspect_util import is_class_method
from ray._private.inspect_util import is_function_or_method
from ray._private.inspect_util import is_static_method
import ray._private.worker
import ray.actor
import ray.dashboard.modules.job.common
import ray.dashboard.modules.job.job_manager
import ray.dashboard.modules.job.job_supervisor
import ray.util.tracing.tracing_helper


class RayTraceProcessor:
    def process_trace(self, trace: List[Span]) -> Optional[List[Span]]:
        if not trace:
            return trace

        processed_trace = []
        for span in trace:
            if span.service != "ray.dashboard":
                processed_trace.append(span)
            if span.get_tag("component") == "ray":
                span.set_metric(_DJM_ENABLED_KEY, 1)
                span.set_metric(_FILTER_KEPT_KEY, 1)
                span.set_metric(_SPAN_MEASURED_KEY, 1)
                span.set_metric(_SAMPLING_PRIORITY_KEY, 2)

        return processed_trace


config._add("ray", dict(_default_service=schematize_service_name("ray")))

def _sort_params_list(params_list: List[Parameter]):
    """Given a list of Parameters, if a kwargs Parameter exists,
    move it to the end of the list."""
    for i, param in enumerate(params_list):
        if param.kind == Parameter.VAR_KEYWORD:
            params_list.append(params_list.pop(i))
            break
    return params_list


def _add_param_to_signature(function: Callable, new_param: Parameter):
    """Add additional Parameter to function signature."""
    old_sig = inspect.signature(function)
    old_sig_list_repr = list(old_sig.parameters.values())
    # If new_param is already in signature, do not add it again.
    if any(param.name == new_param.name for param in old_sig_list_repr):
        return old_sig
    new_params = _sort_params_list(old_sig_list_repr + [new_param])
    new_sig = old_sig.replace(parameters=new_params)
    return new_sig


def get_version() -> str:
    return parse_version(getattr(ray, "__version__", ""))


def _inject_tracing_into_function(function):
    """Inject trace context parameter into function signature"""

    def wrapped_function(*args, **kwargs):
        return _wrap_task_execution(function, *args, **kwargs)

    return wrapped_function


def _inject_dd_tracing_into_runtime_env(serialized_runtime_env_info, current_span):
    def parse_json_string(json_string, default):
        if json_string and json_string.strip() != "{}":
            return json.loads(json_string)
        return default

    try:
        runtime_env_info = parse_json_string(serialized_runtime_env_info, {})
        serialized_runtime_env = runtime_env_info.get("serializedRuntimeEnv", "{}")
        runtime_env = parse_json_string(serialized_runtime_env, {})
        env_vars = runtime_env.get("env_vars", {})

        _TraceContext._inject(current_span.context, env_vars)
        runtime_env["env_vars"] = env_vars
        runtime_env_info["serializedRuntimeEnv"] = json.dumps(runtime_env, sort_keys=True)

        return json.dumps(runtime_env_info, sort_keys=True)

    except (json.JSONDecodeError, KeyError):
        return serialized_runtime_env_info


def _wrap_task_execution(wrapped, *args, **kwargs):
    """
    Wraps the actual execution of a Ray task to trace its performance.
    """
    if not tracer:
        return wrapped(*args, **kwargs)

    # Extract context from parent span
    extracted_context = _TraceContext._extract(
        {
            "traceparent": os.environ.get("traceparent"),
            "tracestate": os.environ.get("tracestate"),
        }
    )
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


def traced_submit_task(wrapped, instance, args, kwargs):
    if not tracer:
        return wrapped(*args, **kwargs)

    # Extract context from parent span
    extracted_context = _TraceContext._extract(
        {
            "traceparent": os.environ.get("traceparent"),
            "tracestate": os.environ.get("tracestate"),
        }
    )
    if not extracted_context:
        return wrapped(*args, **kwargs)

    # Inject function in the function that will be executed
    with instance._inject_lock:
        if not hasattr(instance, "_tracing_injected"):
            instance._function = _inject_tracing_into_function(instance._function)
            instance._tracing_injected = True

    tracer.context_provider.activate(extracted_context)
    with tracer.trace("ray.task.submit", service=os.environ.get("_RAY_SUBMISSION_ID"), span_type=SpanTypes.ML) as span:
        span.set_tag_str("component", "ray")
        span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)
        try:
            updated_serialized_runtime_env_info = _inject_dd_tracing_into_runtime_env(
                kwargs.get("serialized_runtime_env_info", "{}"), span
            )
            kwargs["serialized_runtime_env_info"] = updated_serialized_runtime_env_info

            resp = wrapped(*args, **kwargs)

            span.set_tag_str("ray.task.submit_status", "success")
            return resp
        except Exception as e:
            span.set_tag_str("ray.task.submit_status", "error")
            raise e


def traced_submit_job(wrapped, instance, args, kwargs):
    if not tracer:
        return wrapped(*args, **kwargs)

    with tracer.trace("ray.job.submit", service=kwargs["submission_id"], span_type=SpanTypes.ML) as span:
        span.set_tag_str("component", "ray")
        span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)

        try:
            # Inject the context of the job
            env_vars = kwargs.setdefault("runtime_env", {}).setdefault("env_vars", {})
            _TraceContext._inject(span.context, env_vars)
            env_vars["_RAY_SUBMISSION_ID"] = kwargs.get("submission_id", "")

            resp = wrapped(*args, **kwargs)
            span.set_tag_str("ray.job.submit_status", "success")
            return resp
        except Exception as e:
            span.set_tag_str("ray.job.submit_status", "error")
            raise e

def traced_actor_method_call(wrapped, instance, args, kwargs):
    if not tracer:
        return wrapped(*args, **kwargs)


    method_name = args[0]
    # if _dd_trace_ctx was not injected, we do not want to trace this function
    if not any(p.name == "_dd_trace_ctx" for p in instance._ray_method_signatures[method_name]):
        return wrapped(*args, **kwargs)

    if os.environ.get("traceparent") is not None:
        extracted_context = _TraceContext._extract(
            {
                "traceparent": os.environ.get("traceparent"),
                "tracestate": os.environ.get("tracestate"),
            }
        )
        tracer.context_provider.activate(extracted_context)

    with tracer.trace("ray.actor.method.call", service=os.environ.get("_RAY_SUBMISSION_ID"), span_type=SpanTypes.ML) as span:
        span.set_tag_str("component", "ray")
        span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)

        try:
            headers = {}
            _TraceContext._inject(span.context, headers)
            if "kwargs" not in kwargs or kwargs["kwargs"] is None:
                kwargs["kwargs"] = {}
            kwargs["kwargs"]["_dd_trace_ctx"] = headers

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


def _create_span_wrapper(method: Callable[..., Any]) -> Any:
    """Create a synchronous span wrapper for actor methods."""

    def _traced_method(
        self: Any,
        *_args: Any,
        _dd_trace_ctx = None,
        **_kwargs: Any,
    ) -> Any:
        from ddtrace import tracer as dd_tracer
        from ddtrace.ext import SpanTypes
        from ddtrace.propagation.http import _TraceContext

        if not dd_tracer:
            return method(self, *_args, **_kwargs)

        if _dd_trace_ctx is not None:
            extracted_context = _TraceContext._extract(_dd_trace_ctx)
            dd_tracer.context_provider.activate(extracted_context)
        elif os.environ.get("traceparent") is not None:
            extracted_context = _TraceContext._extract(
                {
                    "traceparent": os.environ.get("traceparent"),
                    "tracestate": os.environ.get("tracestate"),
                }
            )
            dd_tracer.context_provider.activate(extracted_context)

        method_name = f"{self.__class__.__name__}.{method.__name__}"
        with dd_tracer.trace(
            "ray.actor.method", service=os.environ.get("_RAY_SUBMISSION_ID"), span_type=SpanTypes.ML
        ) as span:
            span.set_tag_str("component", "ray")
            span.set_tag_str(SPAN_KIND, SpanKind.CONSUMER)
            span.resource = method_name

            return method(self, *_args, **_kwargs)

    return _traced_method


def _create_async_span_wrapper(method: Callable[..., Any]) -> Any:
    """Create an asynchronous span wrapper for actor methods."""

    async def _traced_async_method(
        self: Any,
        *_args: Any,
        _dd_trace_ctx = None,
        **_kwargs: Any,
    ) -> Any:
        from ddtrace import tracer as dd_tracer
        from ddtrace.ext import SpanTypes
        from ddtrace.propagation.http import _TraceContext

        if not dd_tracer:
            return await method(self, *_args, **_kwargs)

        if _dd_trace_ctx is not None:
            extracted_context = _TraceContext._extract(_dd_trace_ctx)
            dd_tracer.context_provider.activate(extracted_context)
        elif os.environ.get("traceparent") is not None:
            extracted_context = _TraceContext._extract(
                {
                    "traceparent": os.environ.get("traceparent"),
                    "tracestate": os.environ.get("tracestate"),
                }
            )
            dd_tracer.context_provider.activate(extracted_context)

        method_name = f"{self.__class__.__name__}.{method.__name__}"
        with dd_tracer.trace(
            "ray.actor.method", service=os.environ.get("_RAY_SUBMISSION_ID"), span_type=SpanTypes.ML
        ) as span:
            span.set_tag_str("component", "ray")
            span.set_tag_str(SPAN_KIND, SpanKind.CONSUMER)
            span.resource = method_name

            return await method(self, *_args, **_kwargs)

    return _traced_async_method


def _handle_job_supervisor_tracing(cls):
    methods_to_ignore = {"ping", "_polling"}

    def job_supervisor_run_wrapper(method: Callable[..., Any]) -> Any:
        async def _traced_run_method(self: Any, *_args: Any, _dd_trace_ctx = None, **_kwargs: Any) -> Any:
            from ddtrace import tracer as dd_tracer
            from ddtrace.ext import SpanTypes
            from ddtrace.propagation.http import _TraceContext

            if not dd_tracer:
                return await method(self, *_args, **_kwargs)

            if os.environ.get("traceparent") is not None:
                extracted_context = _TraceContext._extract(
                    {
                        "traceparent": os.environ.get("traceparent"),
                        "tracestate": os.environ.get("tracestate"),
                    }
                )
                dd_tracer.context_provider.activate(extracted_context)

            method_name = f"{self.__class__.__name__}.{method.__name__}"
            with dd_tracer.trace(
                "ray.job.run", service=os.environ.get("_RAY_SUBMISSION_ID"), span_type=SpanTypes.ML
            ) as span:
                span.set_tag_str("component", "ray")
                span.set_tag_str(SPAN_KIND, SpanKind.CONSUMER)
                span.resource = method_name

                headers = {}
                _TraceContext._inject(span.context, headers)
                os.environ["traceparent"] = headers.get("traceparent", "")
                os.environ["tracestate"] = headers.get("tracestate", "")

                return await method(self, *_args, **_kwargs)

        return _traced_run_method

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

        method.__signature__ = _add_param_to_signature(
            method,
            inspect.Parameter(
                "_dd_trace_ctx", inspect.Parameter.KEYWORD_ONLY, default=None
            ),
        )

        # Special handling for the run method
        if name == "run" and inspect.iscoroutinefunction(method):
            wrapped_method = wraps(method)(job_supervisor_run_wrapper(method))
        else:
            # Regular tracing for all other methods using shared wrappers
            if inspect.iscoroutinefunction(method):
                wrapped_method = wraps(method)(_create_async_span_wrapper(method))
            else:
                wrapped_method = wraps(method)(_create_span_wrapper(method))

        setattr(cls, name, wrapped_method)

    return cls


def inject_tracing_into_actor_class(wrapped, instance, args, kwargs):
    # Import tracer inside the function to avoid global reference issues
    from ddtrace import tracer as dd_tracer

    if not dd_tracer:
        return wrapped(*args, **kwargs)

    cls = wrapped(*args, **kwargs)
    module_name = str(cls.__module__)
    class_name = str(cls.__name__)
    if module_name.startswith("ray.dag") or module_name.startswith("ray.experimental"):
        return cls

    if f"{module_name}.{class_name}" == "ray.dashboard.modules.job.job_supervisor.JobSupervisor":
        return _handle_job_supervisor_tracing(cls)

    methods = inspect.getmembers(cls, is_function_or_method)
    for name, method in methods:
        if (
            is_static_method(cls, name)
            or is_class_method(method)
            or inspect.isgeneratorfunction(method)
            or inspect.isasyncgenfunction(method)
            or name == "__del__"
        ):
            continue

        method.__signature__ = _add_param_to_signature(
            method,
            inspect.Parameter(
                "_dd_trace_ctx", inspect.Parameter.KEYWORD_ONLY, default=None
            ),
        )

        if inspect.iscoroutinefunction(method):
            wrapped_method = wraps(method)(_create_async_span_wrapper(method))
        else:
            wrapped_method = wraps(method)(_create_span_wrapper(method))

        setattr(cls, name, wrapped_method)

    return cls

def patch():
    if getattr(ray, "_datadog_patch", False):
        return

    ray._datadog_patch = True

    tracer._span_aggregator.user_processors.append(RayTraceProcessor())

    _w(ray.remote_function, "RemoteFunction._remote", traced_submit_task)
    _w(ray._private.worker, "disconnect", flush_worker_spans)
    _w(ray.dashboard.modules.job.job_manager.JobManager, "submit_job", traced_submit_job)
    _w(ray.actor, "_modify_class", inject_tracing_into_actor_class)
    _w(ray.actor.ActorHandle, "_actor_method_call", traced_actor_method_call)


def unpatch():
    if not getattr(ray, "_datadog_patch", False):
        return

    ray._datadog_patch = False

    tracer._span_aggregator.user_processors = [
        p for p in tracer._span_aggregator.user_processors if not isinstance(p, RayTraceProcessor)
    ]

    _u(ray.remote_function, "RemoteFunction._remote", traced_submit_task)
    _u(ray._private.worker, "disconnect", flush_worker_spans)
    _u(ray.dashboard.modules.job.job_manager.JobManager, "submit_job", traced_submit_job)
    _u(ray.actor, "_modify_class", inject_tracing_into_actor_class)
    _u(ray.actor.ActorHandle, "_actor_method_call", traced_actor_method_call)
