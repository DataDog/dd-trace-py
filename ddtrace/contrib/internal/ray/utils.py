import inspect
from inspect import Parameter
from inspect import Signature
import os
import re
from typing import Any
from typing import Callable
from typing import List
from typing import Optional

import ray
from ray.runtime_context import get_runtime_context

from ddtrace.propagation.http import _TraceContext


JOB_NAME_REGEX = re.compile(r"^job\:([A-Za-z0-9_\.\-]+),run:([A-Za-z0-9_\.\-]+)$")


def _inject_dd_trace_ctx_kwarg(method: Callable) -> Signature:
    old_sig = inspect.signature(method)
    if "_dd_trace_ctx" in old_sig.parameters:
        return old_sig

    new_param = Parameter("_dd_trace_ctx", Parameter.KEYWORD_ONLY, default=None)
    params_list = list(old_sig.parameters.values()) + [new_param]
    sorted_params = sorted(params_list, key=lambda p: p.kind == Parameter.VAR_KEYWORD)
    return old_sig.replace(parameters=sorted_params)


def _inject_context_in_kwargs(context, kwargs):
    headers = {}
    _TraceContext._inject(context, headers)
    if "kwargs" not in kwargs or kwargs["kwargs"] is None:
        kwargs["kwargs"] = {}
    kwargs["kwargs"]["_dd_trace_ctx"] = headers


def _inject_context_in_env(context):
    headers = {}
    _TraceContext._inject(context, headers)
    os.environ["traceparent"] = headers.get("traceparent", "")
    os.environ["tracestate"] = headers.get("tracestate", "")


def _extract_tracing_context_from_env():
    if os.environ.get("traceparent") is not None and os.environ.get("tracestate") is not None:
        return _TraceContext._extract(
            {
                "traceparent": os.environ.get("traceparent"),
                "tracestate": os.environ.get("tracestate"),
            }
        )
    return None


def _inject_ray_span_tags(span):
    runtime_context = get_runtime_context()

    span.set_tag_str("component", "ray")
    span.set_tag_str("ray.job_id", runtime_context.get_job_id())
    span.set_tag_str("ray.node_id", runtime_context.get_node_id())

    worker_id = runtime_context.get_worker_id()
    if worker_id is not None:
        span.set_tag_str("ray.worker_id", worker_id)

    if runtime_context.worker.mode == ray._private.worker.WORKER_MODE:
        task_id = runtime_context.get_task_id()
        if task_id is not None:
            span.set_tag_str("ray.task_id", task_id)

    actor_id = runtime_context.get_actor_id()
    if actor_id is not None:
        span.set_tag_str("ray.actor_id", actor_id)

    submission_id = os.environ.get("_RAY_SUBMISSION_ID")
    if submission_id is not None:
        span.set_tag_str("ray.submission_id", submission_id)


# -------------------------------------------------------------------------------------------
# This is extracted from ray code
# it allows to ensure compatibility with older versions of ray still maintained (2.46.0)
# -------------------------------------------------------------------------------------------
def get_signature(func: Any) -> inspect.Signature:
    """Get signature parameters.

    Support Cython functions by grabbing relevant attributes from the Cython
    function and attaching to a no-op function. This is somewhat brittle, since
    inspect may change, but given that inspect is written to a PEP, we hope
    it is relatively stable. Future versions of Python may allow overloading
    the inspect 'isfunction' and 'ismethod' functions / create ABC for Python
    functions. Until then, it appears that Cython won't do anything about
    compatability with the inspect module.

    Args:
        func: The function whose signature should be checked.

    Returns:
        A function signature object, which includes the names of the keyword
            arguments as well as their default values.

    Raises:
        TypeError: A type error if the signature is not supported
    """
    # The first condition for Cython functions, the latter for Cython instance
    # methods
    if is_cython(func):
        attrs = ["__code__", "__annotations__", "__defaults__", "__kwdefaults__"]

        if all(hasattr(func, attr) for attr in attrs):
            original_func = func

            def func():
                return

            for attr in attrs:
                setattr(func, attr, getattr(original_func, attr))
        else:
            raise TypeError(f"{func!r} is not a Python function we can process")

    return inspect.signature(func)


def extract_signature(func: Any, ignore_first: bool = False) -> List[Parameter]:
    """Extract the function signature from the function.

    Args:
        func: The function whose signature should be extracted.
        ignore_first: True if the first argument should be ignored. This should
            be used when func is a method of a class.

    Returns:
        List of Parameter objects representing the function signature.
    """
    signature_parameters = list(get_signature(func).parameters.values())

    if ignore_first:
        if len(signature_parameters) == 0:
            raise ValueError(
                f"Methods must take a 'self' argument, but the method '{func.__name__}' does not have one."
            )
        signature_parameters = signature_parameters[1:]

    return signature_parameters


def is_cython(obj):
    """Check if an object is a Cython function or method"""

    # TODO(suo): We could split these into two functions, one for Cython
    # functions and another for Cython methods.
    # TODO(suo): There doesn't appear to be a Cython function 'type' we can
    # check against via isinstance. Please correct me if I'm wrong.
    def check_cython(x):
        return type(x).__name__ == "cython_function_or_method"

    # Check if function or method, respectively
    return check_cython(obj) or (hasattr(obj, "__func__") and check_cython(obj.__func__))


def get_dd_job_name(submission_id: Optional[str] = None):
    """
    Get the job name from the submission id.
    If the submission id is not a valid job name, return the default job name.
    If the submission id is not set, return the default job name.
    """
    job_name = os.environ.get("_RAY_JOB_NAME")
    if job_name:
        return job_name
    if submission_id is None:
        submission_id = os.environ.get("_RAY_SUBMISSION_ID") or ""
    match = JOB_NAME_REGEX.match(submission_id)
    if match:
        return match.group(1)
    elif submission_id:
        return submission_id
    return None
