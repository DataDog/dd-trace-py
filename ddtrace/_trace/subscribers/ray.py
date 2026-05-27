from types import TracebackType
from typing import Optional

from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.contrib._events.ray import RayContextInjectionEvent
from ddtrace.contrib._events.ray import RayCoreAPIEvent
from ddtrace.contrib._events.ray import RayEvents
from ddtrace.contrib._events.ray import RayExecutionEvent
from ddtrace.contrib._events.ray import RayJobEvent
from ddtrace.contrib._events.ray import RaySubmissionEvent
from ddtrace.contrib.internal.ray.constants import DD_RAY_TRACE_CTX
from ddtrace.contrib.internal.ray.constants import RAY_ACTOR_CLASS_NAME
from ddtrace.contrib.internal.ray.constants import RAY_ACTOR_METHOD_ARGS
from ddtrace.contrib.internal.ray.constants import RAY_ACTOR_METHOD_KWARGS
from ddtrace.contrib.internal.ray.constants import RAY_ACTOR_METHOD_NAME
from ddtrace.contrib.internal.ray.constants import RAY_ACTOR_METHOD_SUBMIT_STATUS
from ddtrace.contrib.internal.ray.constants import RAY_ACTOR_MODULE_NAME
from ddtrace.contrib.internal.ray.constants import RAY_ENTRYPOINT
from ddtrace.contrib.internal.ray.constants import RAY_JOB_NAME
from ddtrace.contrib.internal.ray.constants import RAY_JOB_STATUS
from ddtrace.contrib.internal.ray.constants import RAY_JOB_SUBMIT_STATUS
from ddtrace.contrib.internal.ray.constants import RAY_STATUS_ERROR
from ddtrace.contrib.internal.ray.constants import RAY_STATUS_SUCCESS
from ddtrace.contrib.internal.ray.constants import RAY_SUBMISSION_ID
from ddtrace.contrib.internal.ray.constants import RAY_TASK_ACCELERATOR_TYPE
from ddtrace.contrib.internal.ray.constants import RAY_TASK_ARGS
from ddtrace.contrib.internal.ray.constants import RAY_TASK_FUNCTION_MODULE
from ddtrace.contrib.internal.ray.constants import RAY_TASK_FUNCTION_QUALNAME
from ddtrace.contrib.internal.ray.constants import RAY_TASK_KWARGS
from ddtrace.contrib.internal.ray.constants import RAY_TASK_MAX_RETRIES
from ddtrace.contrib.internal.ray.constants import RAY_TASK_NUM_CPUS
from ddtrace.contrib.internal.ray.constants import RAY_TASK_NUM_GPUS
from ddtrace.contrib.internal.ray.constants import RAY_TASK_NUM_RETURNS
from ddtrace.contrib.internal.ray.constants import RAY_TASK_RESOURCES_PREFIX
from ddtrace.contrib.internal.ray.constants import RAY_TASK_SCHEDULING_STRATEGY
from ddtrace.contrib.internal.ray.constants import RAY_TASK_SUBMIT_STATUS
from ddtrace.contrib.internal.ray.core.utils import _extract_tracing_context_from_env
from ddtrace.contrib.internal.ray.core.utils import _inject_context_in_kwargs
from ddtrace.contrib.internal.ray.core.utils import _set_dist_ai_metrics
from ddtrace.contrib.internal.ray.core.utils import _set_runtime_context_attributes
from ddtrace.contrib.internal.ray.core.utils import flatten_metadata_dict
from ddtrace.contrib.internal.ray.core.utils import set_tag_or_truncate
from ddtrace.contrib.internal.ray.span_manager import start_long_running_job
from ddtrace.contrib.internal.ray.span_manager import start_long_running_span
from ddtrace.contrib.internal.ray.span_manager import stop_long_running_job
from ddtrace.contrib.internal.ray.span_manager import stop_long_running_span
from ddtrace.internal import core
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.logger import get_logger
from ddtrace.propagation.http import _TraceContext
from ddtrace.trace import tracer


log = get_logger(__name__)


class RayJobStartSubscriber(TracingSubscriber):
    """Subscriber for ray.job context"""

    event_names = (RayEvents.RAY_JOB.value,)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext) -> None:
        event: RayJobEvent = ctx.event
        submission_id = event.submission_id

        job_span = ctx.span
        _set_dist_ai_metrics(job_span)
        _set_runtime_context_attributes(job_span, submission_id)
        start_long_running_job(job_span)

        if event.entrypoint:
            job_span._set_attribute(RAY_ENTRYPOINT, event.entrypoint)

        dot_paths = flatten_metadata_dict(event.metadata)
        for k, v in dot_paths.items():
            set_tag_or_truncate(job_span, k, v)

        # Inject context in ray environment variables
        env = event.environment_variables
        _TraceContext._inject(job_span.context, env)
        env[RAY_SUBMISSION_ID] = submission_id
        if event.job_name:
            env[RAY_JOB_NAME] = event.job_name

        job_span._set_attribute(RAY_JOB_SUBMIT_STATUS, RAY_STATUS_SUCCESS)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event: RayJobEvent = ctx.event
        job_span = ctx.span

        # a simple if event.submit_failed does not work on event_field
        if event.submit_failed is True:
            job_span._set_attribute(RAY_JOB_SUBMIT_STATUS, RAY_STATUS_ERROR)

        exc_type, exc_val, exc_tb = exc_info
        if exc_type is not None and exc_val is not None:
            job_span.set_exc_info(exc_type, exc_val, exc_tb)
            job_span._set_attribute(RAY_JOB_STATUS, RAY_STATUS_ERROR)

        stop_long_running_job(event.submission_id, event.ended_job_info)


class RayExecutionSubscriber(TracingSubscriber):
    """Subscriber for ray.execute context"""

    event_names = (RayEvents.RAY_EXECUTE.value,)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext) -> None:
        span = ctx.span
        _set_dist_ai_metrics(span)
        _set_runtime_context_attributes(span)

        event: RayExecutionEvent = ctx.event
        if event.integration_config.trace_args_kwargs:
            args_tag = RAY_ACTOR_METHOD_ARGS if event.is_actor_method else RAY_TASK_ARGS
            kwargs_tag = RAY_ACTOR_METHOD_KWARGS if event.is_actor_method else RAY_TASK_KWARGS

            method_kwargs = event.method_kwargs
            if DD_RAY_TRACE_CTX in method_kwargs:
                method_kwargs = {k: v for k, v in method_kwargs.items() if k != DD_RAY_TRACE_CTX}

            set_tag_or_truncate(span, args_tag, event.method_args)
            set_tag_or_truncate(span, kwargs_tag, method_kwargs)

        # Stamp actor identity tags only for actor-method executions.
        if event.is_actor_method:
            try:
                if getattr(event, "actor_class_name", None):
                    span.set_tag(RAY_ACTOR_CLASS_NAME, str(event.actor_class_name))
                if getattr(event, "actor_module_name", None):
                    span.set_tag(RAY_ACTOR_MODULE_NAME, str(event.actor_module_name))
                if getattr(event, "actor_method_name", None):
                    span.set_tag(RAY_ACTOR_METHOD_NAME, str(event.actor_method_name))
            except Exception:
                log.debug("Failed to set actor metadata tags on execution span", exc_info=True)

        # Execution spans are finalized manually to support async/remote execution
        # boundaries that do not align with context manager auto-finish.
        start_long_running_span(span)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        span = ctx.span

        exc_type, exc_val, exc_tb = exc_info
        if exc_type is not None and exc_val is not None:
            span.set_exc_info(exc_type, exc_val, exc_tb)

        stop_long_running_span(span)


class RayCoreAPITracingSubscriber(TracingSubscriber):
    """Subscriber for core API calls (ray.get/wait/put)."""

    event_names = (RayEvents.RAY_CORE_API.value,)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext) -> None:
        event: RayCoreAPIEvent = ctx.event
        span = ctx.span

        _set_dist_ai_metrics(span)
        _set_runtime_context_attributes(span)

        if event.is_long_running:
            start_long_running_span(span)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event: RayCoreAPIEvent = ctx.event
        span = ctx.span

        exc_type, exc_val, exc_tb = exc_info
        if exc_type is not None and exc_val is not None:
            span.set_exc_info(exc_type, exc_val, exc_tb)

        if event.is_long_running:
            stop_long_running_span(span)
        else:
            # Non-blocking APIs can be safely closed at end-of-callback.
            span.finish()


class RaySubmissionSubscriber(TracingSubscriber):
    """Subscriber for task and actor method submission tracing."""

    event_names = (RayEvents.RAY_SUBMIT.value,)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext) -> None:
        event: RaySubmissionEvent = ctx.event
        span = ctx.span

        _set_dist_ai_metrics(span)
        _set_runtime_context_attributes(span)

        if event.integration_config.trace_args_kwargs:
            args_tag = RAY_TASK_ARGS if event.is_task_submission else RAY_ACTOR_METHOD_ARGS
            kwargs_tag = RAY_TASK_KWARGS if event.is_task_submission else RAY_ACTOR_METHOD_KWARGS
            set_tag_or_truncate(span, args_tag, event.method_args)
            set_tag_or_truncate(span, kwargs_tag, event.method_kwargs)

        # Stamp scheduling-hint tags only for task submissions.
        if event.is_task_submission:
            try:
                if event.task_function_module:
                    span.set_tag(RAY_TASK_FUNCTION_MODULE, str(event.task_function_module))
                if event.task_function_qualname:
                    span.set_tag(RAY_TASK_FUNCTION_QUALNAME, str(event.task_function_qualname))
                if event.task_num_cpus is not None:
                    span._set_attribute(RAY_TASK_NUM_CPUS, float(event.task_num_cpus))
                if event.task_num_gpus is not None:
                    span._set_attribute(RAY_TASK_NUM_GPUS, float(event.task_num_gpus))
                if event.task_num_returns is not None:
                    span._set_attribute(RAY_TASK_NUM_RETURNS, int(event.task_num_returns))
                if event.task_max_retries is not None:
                    span._set_attribute(RAY_TASK_MAX_RETRIES, int(event.task_max_retries))
                if event.task_accelerator_type:
                    span.set_tag(RAY_TASK_ACCELERATOR_TYPE, str(event.task_accelerator_type))
                if event.task_scheduling_strategy:
                    span.set_tag(RAY_TASK_SCHEDULING_STRATEGY, str(event.task_scheduling_strategy))
                if event.task_resources:
                    for k, v in event.task_resources.items():
                        try:
                            span._set_attribute(f"{RAY_TASK_RESOURCES_PREFIX}{k}", float(v))
                        except Exception:
                            pass
            except Exception:
                log.debug("Failed to set task scheduling tags", exc_info=True)

        # Stamp actor identity tags only for actor-method submissions.
        if event.is_actor_method:
            try:
                if getattr(event, "actor_class_name", None):
                    span.set_tag(RAY_ACTOR_CLASS_NAME, str(event.actor_class_name))
                if getattr(event, "actor_module_name", None):
                    span.set_tag(RAY_ACTOR_MODULE_NAME, str(event.actor_module_name))
                if getattr(event, "actor_method_name", None):
                    span.set_tag(RAY_ACTOR_METHOD_NAME, str(event.actor_method_name))
            except Exception:
                log.debug("Failed to set actor metadata tags on submission span", exc_info=True)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event: RaySubmissionEvent = ctx.event
        span = ctx.span

        status_tag = RAY_TASK_SUBMIT_STATUS if event.is_task_submission else RAY_ACTOR_METHOD_SUBMIT_STATUS
        span._set_attribute(status_tag, RAY_STATUS_ERROR if exc_info[1] is not None else RAY_STATUS_SUCCESS)


class RayContextInjectionSubscriber(Subscriber):
    """Prepare context for task/actor submission before submitting to Ray."""

    event_names = (RayEvents.RAY_CONTEXT_INJECTION.value,)

    @classmethod
    def on_event(cls, event_instance: RayContextInjectionEvent) -> None:
        event_instance.current_context = tracer.current_trace_context() or _extract_tracing_context_from_env()

        if event_instance.current_context is not None:
            _inject_context_in_kwargs(event_instance.current_context, event_instance.kwargs)
