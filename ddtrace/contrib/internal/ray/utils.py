import inspect
from inspect import Parameter
from inspect import Signature
import json
import os
import re
import socket
import sys
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional

import ray
from ray.runtime_context import get_runtime_context

from ddtrace._trace._limits import MAX_SPAN_META_VALUE_LEN
from ddtrace._trace.context import Context
from ddtrace._trace.span import Span
from ddtrace.constants import _AI_OBS_ENABLED_KEY
from ddtrace.constants import _DJM_ENABLED_KEY
from ddtrace.constants import _FILTER_KEPT_KEY
from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.propagation.http import _TraceContext

from .constants import DD_RAY_TRACE_CTX
from .constants import RAY_ACTOR_ID
from .constants import RAY_COMPONENT
from .constants import RAY_HOSTNAME
from .constants import RAY_JOB_ID
from .constants import RAY_METADATA_PREFIX
from .constants import RAY_NODE_ID
from .constants import RAY_SUBMISSION_ID
from .constants import RAY_SUBMISSION_ID_TAG
from .constants import RAY_TASK_ID
from .constants import RAY_WORKER_ID
from .constants import REDACTED_PATH
from .constants import REDACTED_VALUE


# The job name regex serves to convert a submission ID in the format job:train_my_model,run:1758573287
# to the job name train_my_model
JOB_NAME_REGEX = re.compile(r"^job\:([A-Za-z0-9_\.\-]+),run:([A-Za-z0-9_\.\-]+)$")
# The entry point regex is intended to extract the name of the Python script from a Ray entrypoint,
# for example, if the entrypoint is python3 woof.py --breed mutt
# then the job name will be woof
ENTRY_POINT_REGEX = re.compile(r"([^\s\/\\]+)\.py")


def _inject_dd_trace_ctx_kwarg(method: Callable) -> Signature:
    old_sig = inspect.signature(method)
    if DD_RAY_TRACE_CTX in old_sig.parameters:
        return old_sig

    new_param = Parameter(DD_RAY_TRACE_CTX, Parameter.KEYWORD_ONLY, default=None)
    params_list = list(old_sig.parameters.values()) + [new_param]
    sorted_params = sorted(params_list, key=lambda p: p.kind == Parameter.VAR_KEYWORD)
    return old_sig.replace(parameters=sorted_params)


def _inject_context_in_kwargs(context: Context, kwargs: Dict[str, Any]) -> None:
    headers = {}
    _TraceContext._inject(context, headers)
    if "kwargs" not in kwargs or kwargs["kwargs"] is None:
        kwargs["kwargs"] = {}
    kwargs["kwargs"][DD_RAY_TRACE_CTX] = headers


def _inject_context_in_env(context: Context) -> None:
    headers = {}
    _TraceContext._inject(context, headers)
    os.environ["traceparent"] = headers.get("traceparent", "")
    os.environ["tracestate"] = headers.get("tracestate", "")


def _extract_tracing_context_from_env() -> Optional[Context]:
    if os.environ.get("traceparent") is not None and os.environ.get("tracestate") is not None:
        return _TraceContext._extract(
            {
                "traceparent": os.environ.get("traceparent"),
                "tracestate": os.environ.get("tracestate"),
            }
        )
    return None


def _inject_ray_span_tags_and_metrics(span: Span) -> None:
    span.set_tag_str("component", RAY_COMPONENT)
    span.set_tag_str(RAY_HOSTNAME, socket.gethostname())
    span.set_metric(_AI_OBS_ENABLED_KEY, 1)
    span.set_metric(_DJM_ENABLED_KEY, 1)
    span.set_metric(_FILTER_KEPT_KEY, 1)
    span.set_metric(_SPAN_MEASURED_KEY, 1)
    span.set_metric(_SAMPLING_PRIORITY_KEY, 2)

    submission_id = os.environ.get(RAY_SUBMISSION_ID)
    if submission_id is not None:
        span.set_tag_str(RAY_SUBMISSION_ID_TAG, submission_id)

    if ray.is_initialized():
        runtime_context = get_runtime_context()

        span.set_tag_str(RAY_JOB_ID, runtime_context.get_job_id())
        span.set_tag_str(RAY_NODE_ID, runtime_context.get_node_id())

        worker_id = runtime_context.get_worker_id()
        if worker_id is not None:
            span.set_tag_str(RAY_WORKER_ID, worker_id)

        if runtime_context.worker.mode == ray._private.worker.WORKER_MODE:
            task_id = runtime_context.get_task_id()
            if task_id is not None:
                span.set_tag_str(RAY_TASK_ID, task_id)

        actor_id = runtime_context.get_actor_id()
        if actor_id is not None:
            span.set_tag_str(RAY_ACTOR_ID, actor_id)


def set_tag_or_truncate(span: Span, tag_name: str, tag_value: Any = None) -> None:
    """We want to add args/kwargs values as tag when we execute a task/actor method.
    However they might be really big. In that case we dont way to serialize them AT ALL
    and we do not want to rely on _encoding.pyx.
    """
    if sys.getsizeof(tag_value) > MAX_SPAN_META_VALUE_LEN:
        span.set_tag(tag_name, REDACTED_VALUE)
    else:
        span.set_tag(tag_name, tag_value)


def get_dd_job_name_from_entrypoint(entrypoint: str):
    """
    Get the job name from the entrypoint.
    """
    match = ENTRY_POINT_REGEX.search(entrypoint)
    if match:
        return match.group(1)
    return None


def redact_paths(s: str) -> str:
    """
    Redact path-like substrings from an entry-point string.
    Uses os.sep (and os.altsep if present) to detect paths; preserves spacing.
    """

    def _redact_pathlike(s):
        """
        If s contains a path separator, replace the directory part with REDACTION,
        preserving the final component (basename). Trailing separators are ignored.
        Detects both os.sep and os.altsep if present.
        """

        # Pick the actual separator used in this token (prefer os.sep if both appear)
        used_sep = os.sep if (os.sep in s) else (os.altsep if (os.altsep and os.altsep in s) else None)
        if not used_sep:
            return s

        core = s.rstrip(used_sep)
        if not core:
            return REDACTED_PATH

        basename = core.split(used_sep)[-1]
        return f"{REDACTED_PATH}{used_sep}{basename}"

    def _redact_token(tok) -> str:
        # key=value (value may be quoted)
        if "=" in tok:
            key, val = tok.split("=", 1)
            if len(val) >= 2 and val[0] == val[-1] and val[0] in {"'", '"'}:
                q = val[0]
                inner = val[1:-1]
                return f"{key}={q}{_redact_pathlike(inner)}{q}"
            return f"{key}={_redact_pathlike(val)}"

        # Whole token may be quoted
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in {"'", '"'}:
            q = tok[0]
            inner = tok[1:-1]
            return f"{q}{_redact_pathlike(inner)}{q}"

        return _redact_pathlike(tok)

    parts = re.split(r"(\s+)", s)  # keep whitespace
    return "".join(part if part.strip() == "" else _redact_token(part) for part in parts)


def flatten_metadata_dict(data: dict) -> Dict[str, Any]:
    """
    Converts a JSON (or Python dictionary) structure into a dict mapping
    dot-notation paths to leaf values, with keys prefixed once by RAY_METADATA_PREFIX.

    - Assumes the top-level is a dictionary. If a list is encountered anywhere,
      it is stringified with json.dumps and treated as a leaf (no recursion into list elements).
    - Leaf values (str, int, float, bool, None) are returned as-is as the dict values.
    - Returned dict keys are prefixed once with RAY_METADATA_PREFIX.
    """

    if not isinstance(data, dict):
        return {}

    result = {}

    def _recurse(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                new_path = f"{path}.{key}" if path else key
                _recurse(value, new_path)
        elif isinstance(node, list):
            # Treat any list as a leaf by stringifying it
            try:
                list_dump = json.dumps(node, ensure_ascii=False)
            except Exception:
                list_dump = "[]"
            result[path] = list_dump
        else:
            # leaf node: store the accumulated path -> value
            result[path] = node

    _recurse(data, RAY_METADATA_PREFIX)

    return result


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
