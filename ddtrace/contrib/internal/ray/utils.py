import inspect
from inspect import Parameter
from inspect import Signature
import os
from typing import Callable

from ddtrace.propagation.http import _TraceContext
from ray.runtime_context import get_runtime_context


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

    task_id = runtime_context.get_task_id()
    if task_id is not None:
        span.set_tag_str("ray.task_id", task_id)

    actor_id = runtime_context.get_actor_id()
    if actor_id is not None:
        span.set_tag_str("ray.actor_id", actor_id)

    submission_id = os.environ.get("_RAY_SUBMISSION_ID")
    if submission_id is not None:
        span.set_tag_str("ray.submission_id", submission_id)
