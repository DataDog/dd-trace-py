"""
Ray integration for Datadog APM.

This module provides tracing for Ray distributed computing operations.
"""

import os
import inspect
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from inspect import Parameter
from types import ModuleType
import time
import atexit
from typing import (
    Any,
    Callable,
    Dict,
    Generator,
    List,
    MutableMapping,
    Optional,
    Sequence,
    Union,
    cast,
)
import ray._private.worker
import ray.dashboard.modules.job.job_manager
import ray.job_config
import ray.job_submission
from wrapt import wrap_function_wrapper as _w
from wrapt import decorator
import ray

from ray.runtime_context import get_runtime_context

from ddtrace.trace import Pin
from ddtrace import config
from ddtrace.ext import SpanTypes
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.internal.logger import get_logger
from .utils import _sort_params_list, extract_signature


log = get_logger(__name__)

# Configuration
config._add(
    "ray",
    dict(
        _default_service=schematize_service_name("ray"),
        enabled=asbool(os.getenv("DD_RAY_ENABLED", True)),
        trace_tasks=asbool(os.getenv("DD_RAY_TRACE_TASKS", True)),
        trace_actors=asbool(os.getenv("DD_RAY_TRACE_ACTORS", False)),
        trace_objects=asbool(os.getenv("DD_RAY_TRACE_OBJECTS", False)),
    ),
)

# Use a hardcoded service name to avoid recursion during config setup
RAY_SERVICE_NAME = schematize_service_name("ray")


def get_version():
    # type: () -> str
    try:
        import ray

        return ray.__version__
    except ImportError:
        return ""

def _wrap_task_execution(wrapped, *args, **kwargs):
    """
    Wraps the actual execution of a Ray task to trace its performance.

    This function creates a span for the task execution itself, capturing
    function name, arguments, and execution context.
    This runs on the Ray worker when the task is executed.
    """
    # Use the captured pin from the invocation context
    worker_pin = Pin(service=RAY_SERVICE_NAME)
    if not worker_pin.enabled():
        return wrapped(*args, **kwargs)

    # Extract function information for tracing
    function_name = getattr(wrapped, '__name__', 'unknown_function')
    function_module = getattr(wrapped, '__module__', 'unknown_module')

    ray_ctx = get_runtime_context()
    job_id = ray_ctx.get_job_id()
    task_id = ray_ctx.get_task_id()
    worker_id = ray_ctx.get_worker_id()

    print(os.getenv('_RAY_SUBMISSION_ID', None))
    with worker_pin.tracer.trace(
        "ray.task.execute",
        service=RAY_SERVICE_NAME,
        span_type=SpanTypes.WORKER
    ) as span:
        span.set_tag_str("ray.task.function", function_name)
        span.set_tag_str("ray.task.module", function_module)
        span.set_tag_str("component", "ray")
        span.set_tag_str("ray.job_id", str(job_id))
        span.set_tag_str("ray.task_id", str(task_id))
        span.set_tag_str("ray.worker_id", str(worker_id))

        # Add resource name for better visibility
        span.resource = f"{function_module}.{function_name}"

        # Capture number of arguments (without exposing sensitive data)
        span.set_tag_str("ray.task.args_count", str(len(args)))
        span.set_tag_str("ray.task.kwargs_count", str(len(kwargs)))

        try:
            result = wrapped(*args, **kwargs)
            span.set_tag_str("ray.task.status", "success")
            return result
        except Exception as e:
            span.set_tag_str("ray.task.status", "error")
            span.set_tag_str("ray.task.error_type", type(e).__name__)
            span.set_tag_str("ray.task.error_message", str(e))
            raise

def _inject_tracing_into_function(function):
    """Inject trace context parameter into function signature"""
    # old_sig = inspect.signature(function)
    # old_params = list(old_sig.parameters.values())
    # new_param = inspect.Parameter("_dd_ray_trace_ctx", inspect.Parameter.KEYWORD_ONLY, default=None)
    # new_params = _sort_params_list(old_params + [new_param])
    # new_sig = old_sig.replace(parameters=new_params)
    # function.__signature__ = new_sig

    def wrapped_function(*args, **kwargs):
        return _wrap_task_execution(function, *args, **kwargs)

    return wrapped_function

def _wrap_task_invocation(wrapped, instance, args, kwargs):
    """
    Wraps Ray remote function execution to trace task submission and execution.

    This function traces the submission of remote tasks to the Ray cluster,
    including task metadata, resource requirements, and execution context.
    """
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    # Ensure function signature is modified to accept trace context
    with instance._inject_lock:
        if instance._function_signature is None:
            instance._function = _inject_tracing_into_function(instance._function)
            instance._function_signature = extract_signature(instance._function)

    # Trace the task submission (this happens on the client)
    with pin.tracer.trace("ray.task.submit", service=RAY_SERVICE_NAME, span_type=SpanTypes.SERVING) as span:
        # Extract function information for tracing
        function_name = getattr(instance._function, '__name__', 'unknown_function')
        function_module = getattr(instance._function, '__module__', 'unknown_module')
        span.set_tag_str("ray.task.function", function_name)
        span.set_tag_str("ray.task.module", function_module)
        span.set_tag_str("component", "ray")
        span.set_tag_str("ray.task.args_count", str(len(args)))
        span.set_tag_str("ray.task.kwargs_count", str(len(kwargs)))
        span.set_tag_str("ray.task.uuid", str(instance._uuid))

        # # Add resource name for better visibility
        # span.resource = f"{function_module}.{function_name}"

        try:
            resp = wrapped(*args, **kwargs)
            span.set_tag_str("ray.task.submit_status", "success")
            return resp
        except Exception as e:
            span.set_tag_str("ray.task.submit_status", "error")
            span.set_tag_str("ray.task.error_type", type(e).__name__)
            span.set_tag_str("ray.task.error_message", str(e))
            raise

def _wrap_submit(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    # Inject dd tracing in job using runtime_env
    dd_env_vars = {k: v for k, v in os.environ.items() if k.upper().startswith("DD_")}

    runtime_env = kwargs.get('runtime_env', {})

    env_vars = runtime_env.get('env_vars', {})
    env_vars.update(dd_env_vars)
    if kwargs['submission_id'] is not None:
        env_vars['_RAY_SUBMISSION_ID'] = kwargs['submission_id']

    runtime_env['env_vars'] = env_vars

    # Also modify the entrypoint to use ddtrace-run
    entrypoint = kwargs['entrypoint']
    if not entrypoint.startswith("ddtrace-run "):
        kwargs['entrypoint'] = "ddtrace-run " + entrypoint

    with pin.tracer.trace("ray.job.submit", service=RAY_SERVICE_NAME, span_type=SpanTypes.SERVING) as span:
        span.set_tag_str("component", "ray")
        try:
            resp = wrapped(*args, **kwargs)
            span.set_tag_str("ray.job.submit_status", "success")
            return resp
        except Exception as e:
            span.set_tag_str("ray.job.submit_status", "error")
            span.set_tag_str("ray.job.error_type", type(e).__name__)
            span.set_tag_str("ray.job.error_message", str(e))
            raise

def _wrap_disconnect(wrapped, instance, args, kwargs):
    # Use the captured pin from the invocation context
    worker_pin = Pin(service=RAY_SERVICE_NAME)
    if not worker_pin.enabled():
        return wrapped(*args, **kwargs)

    time.sleep(0.5)
    return wrapped(*args, **kwargs)

def patch():
    """
    Patch Ray modules to enable tracing.

    This function patches the necessary Ray modules to inject tracing
    into remote function execution, actor operations, and object store operations.
    """
    if getattr(ray, "_datadog_patch", False):
        return

    ray._datadog_patch = True
    pin = Pin()

    # Patch remote function execution
    if config.ray.trace_tasks:
        _w(ray.remote_function, "RemoteFunction._remote", _wrap_task_invocation)
        _w(ray._private.worker, "disconnect", _wrap_disconnect)
        pin.onto(ray.remote_function.RemoteFunction)

    _w(ray.job_submission.JobSubmissionClient, "submit_job", _wrap_submit)
    pin.onto(ray.job_submission.JobSubmissionClient)

    # Patch actor operations
    if config.ray.trace_actors:
        _w(ray.actor, "ActorClass.remote", _wrap_actor_creation)
        _w(ray.actor, "ActorMethod._remote", _wrap_actor_method_call)

    # Patch object store operations
    if config.ray.trace_objects:
        _w(ray._private.worker, "get", _wrap_get_operation)
        _w(ray._private.worker, "put", _wrap_put_operation)
        _w(ray._private.worker, "wait", _wrap_wait_operation)


def unpatch():
    """
    Remove Ray tracing patches.

    This function removes all tracing patches applied to Ray modules.
    """
    if not getattr(ray, "_datadog_patch", False):
        return

    ray._datadog_patch = False

    # Unpatch remote function execution
    if config.ray.trace_tasks:
        _u(ray.remote_function, "RemoteFunction._remote", _wrap_task_invocation)
        _u(ray._private.worker, "disconnect", _wrap_disconnect)

    _u(ray.job_submission.JobSubmissionClient, "submit_job", _wrap_submit)
    # _u(ray.dashboard.modules.job.job_manager.JobManager, "submit_job", _wrap_submit)

    # Unpatch actor operations
    if config.ray.trace_actors:
        _u(ray.actor, "ActorClass.remote", _wrap_actor_creation)
        _u(ray.actor, "ActorMethod._remote", _wrap_actor_method_call)

    # Unpatch object store operations
    if config.ray.trace_objects:
        _u(ray._private.worker, "get", _wrap_get_operation)
        _u(ray._private.worker, "put", _wrap_put_operation)
        _u(ray._private.worker, "wait", _wrap_wait_operation)
