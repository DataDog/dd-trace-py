from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
import logging
import os
import threading
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional

from pythonjsonlogger.json import JsonFormatter
import ray
from ray._private.inspect_util import is_class_method
from ray._private.inspect_util import is_function_or_method
from ray._private.inspect_util import is_static_method
import ray.actor
import ray.dashboard.modules.job.job_manager
from ray.dashboard.modules.job.job_manager import generate_job_id
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
from ddtrace.internal.constants import LOG_ATTR_ENV
from ddtrace.internal.constants import LOG_ATTR_SERVICE
from ddtrace.internal.constants import LOG_ATTR_SPAN_ID
from ddtrace.internal.constants import LOG_ATTR_TRACE_ID
from ddtrace.internal.constants import LOG_ATTR_VERSION
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.propagation.http import _TraceContext
from ddtrace.settings._config import _get_config

from .utils import _extract_tracing_context_from_env
from .utils import _inject_context_in_env
from .utils import _inject_context_in_kwargs
from .utils import _inject_dd_trace_ctx_kwarg
from .utils import _inject_ray_span_tags
from .utils import extract_signature


LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] - %(message)s"

ray_job_submission_id = ContextVar("ray_job_submission_id")

logger = logging.getLogger()
try:
    logger.setLevel(os.environ.get("DD_TRACE_RAY_LOG_LEVEL", "INFO").upper())
except ValueError:
    logger.setLevel(logging.INFO)

config._add(
    "ray",
    dict(
        _default_service=schematize_service_name("ray"),
        ray_spans_only=asbool(_get_config("DD_TRACE_RAY_SPANS_ONLY", default=True)),
    ),
)


def _supported_versions() -> Dict[str, str]:
    return {"ray": ">=2.46.0"}


def get_version() -> str:
    return str(getattr(ray, "__version__", ""))


class _JobSpanManager:
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

        filtered_spans = []
        for span in trace:
            if span.service == "ray.dashboard" and span.get_tag("component") != "ray":
                continue
            if span.get_tag("component") == "ray":
                span.set_metric(_DJM_ENABLED_KEY, 1)
                span.set_metric(_FILTER_KEPT_KEY, 1)
                span.set_metric(_SPAN_MEASURED_KEY, 1)
                span.set_metric(_SAMPLING_PRIORITY_KEY, 2)
            filtered_spans.append(span)
        return filtered_spans


RAY_JOB_MANAGER_LOGGER_NAME = "ray.dashboard.modules.job.job_manager"
RAY_JOB_MANAGER_JOB_START_MESSAGE_PREFIX = "Starting job with submission_id: "


class ActiveSpanFilter(logging.Filter):
    def filter(self, record):

        context = tracer.get_log_correlation_context()

        # Only capture log lines from inside an active Ray traces
        if context.get(LOG_ATTR_TRACE_ID) and context.get(LOG_ATTR_TRACE_ID) != "0":
            job_submission_id = os.environ.get("_RAY_SUBMISSION_ID")
            # We can get the Ray job submission id out of Ray job_manager's initial message
            # before it is avaiable in the runtime env:
            if not job_submission_id:
                if record.name == RAY_JOB_MANAGER_LOGGER_NAME and record.message.startswith(
                    RAY_JOB_MANAGER_JOB_START_MESSAGE_PREFIX
                ):
                    _, _, job_submission_id = record.message.partition(RAY_JOB_MANAGER_JOB_START_MESSAGE_PREFIX)
                    if job_submission_id:
                        ray_job_submission_id.set(job_submission_id)
                else:
                    job_submission_id = ray_job_submission_id.get()

            record.__setattr__("dd.trace_id", context.get(LOG_ATTR_TRACE_ID))
            record.__setattr__("dd.span_id", context.get(LOG_ATTR_SPAN_ID))
            record.__setattr__("dd.env", context.get(LOG_ATTR_ENV))
            record.__setattr__("dd.service", context.get(LOG_ATTR_SERVICE))
            record.__setattr__("dd.version", context.get(LOG_ATTR_VERSION))

            record.__setattr__("ray.job_submission_id", job_submission_id)
            record.source = "ray"
            record.team = "aiobs"

            return True

        else:
            return False


def _inject_tracing_into_remote_function(function):
    """Inject trace context parameter into function signature"""

    @wraps(function)
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
    tracer.context_provider.activate(extracted_context)

    function_name = getattr(wrapped, "__name__", "unknown_function")
    function_module = getattr(wrapped, "__module__", "unknown_module")

    with tracer.trace(
        f"{function_module}.{function_name}", service=os.environ.get("_RAY_SUBMISSION_ID"), span_type=SpanTypes.RAY
    ) as span:
        span.set_tag_str(SPAN_KIND, SpanKind.CONSUMER)
        _inject_ray_span_tags(span)

        try:
            result = wrapped(*args, **kwargs)
            span.set_tag_str("ray.task.status", "success")
            return result
        except Exception as e:
            span.set_tag_str("ray.task.status", "error")
            raise e


def traced_submit_task(wrapped, instance, args, kwargs):
    """Trace task submission, i.e the func.remote() call"""

    if not tracer:
        return wrapped(*args, **kwargs)

    if tracer.current_span() is None:
        tracer.context_provider.activate(_extract_tracing_context_from_env())

    # Inject dd_trace_ctx args in the function being executed by ray
    with instance._inject_lock:
        if instance._function_signature is None:
            instance._function = _inject_tracing_into_remote_function(instance._function)
            instance._function.__signature__ = _inject_dd_trace_ctx_kwarg(instance._function)
            instance._function_signature = extract_signature(instance._function)

    with tracer.trace(
        f"{instance._function_name}.remote()", service=os.environ.get("_RAY_SUBMISSION_ID"), span_type=SpanTypes.RAY
    ) as span:
        span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)
        _inject_ray_span_tags(span)

        try:
            _inject_context_in_kwargs(span.context, kwargs)

            resp = wrapped(*args, **kwargs)

            span.set_tag_str("ray.task.submit_status", "success")
            return resp
        except Exception as e:
            span.set_tag_str("ray.task.submit_status", "error")
            raise e


def traced_submit_job(wrapped, instance, args, kwargs):
    """Trace job submission. This function is also responsible
    of creating the root span.
    It will also inject _RAY_SUBMISSION_ID
    in the env variable as some spans will not have access to it
    trough ray_ctx
    """

    if not tracer:
        return wrapped(*args, **kwargs)

    submission_id = kwargs.get("submission_id") or generate_job_id()
    kwargs["submission_id"] = submission_id

    # Root span creation
    job_span = tracer.start_span("ray.job", service=submission_id, span_type=SpanTypes.RAY)
    job_span.set_tag_str("component", "ray")
    job_span.set_tag_str("ray.submission_id", submission_id)
    # This will allow to finish the span at the end of the job
    _job_span_manager.add_span(submission_id, job_span)

    # Set global span as the root span
    tracer.context_provider.activate(job_span)
    try:
        with tracer.trace("ray.job.submit", service=submission_id, span_type=SpanTypes.RAY) as submit_span:
            submit_span.set_tag_str("component", "ray")
            submit_span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)

            # Inject the context of the job so that ray.job.run is its child
            env_vars = kwargs.setdefault("runtime_env", {}).setdefault("env_vars", {})
            _TraceContext._inject(job_span.context, env_vars)
            env_vars["_RAY_SUBMISSION_ID"] = submission_id

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
        _job_span_manager.remove_span(submission_id)
        job_span.finish()
        raise e


def traced_actor_method_call(wrapped, instance, args, kwargs):
    """Trace actor method submission, i.e the Actor.func.remote()
    call
    """
    if not tracer:
        return wrapped(*args, **kwargs)

    actor_name = instance._ray_actor_creation_function_descriptor.class_name
    method_name = args[0]

    # if _dd_trace_ctx was not injected in the param of the function, it means
    # we do not want to trace this function, for example: JobSupervisor.ping
    if not any(p.name == "_dd_trace_ctx" for p in instance._ray_method_signatures[method_name]):
        return wrapped(*args, **kwargs)

    if tracer.current_span() is None:
        tracer.context_provider.activate(_extract_tracing_context_from_env())

    with tracer.trace(
        f"{actor_name}.{method_name}.remote()", service=os.environ.get("_RAY_SUBMISSION_ID"), span_type=SpanTypes.RAY
    ) as span:
        span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)
        _inject_ray_span_tags(span)

        try:
            _inject_context_in_kwargs(span.context, kwargs)
            return wrapped(*args, **kwargs)
        except Exception as e:
            raise e


def job_supervisor_run_wrapper(method: Callable[..., Any]) -> Any:
    async def _traced_run_method(self: Any, *args: Any, _dd_trace_ctx=None, **kwargs: Any) -> Any:
        from ddtrace import tracer as dd_tracer
        from ddtrace.ext import SpanTypes

        if not dd_tracer or _dd_trace_ctx is None:
            return await method(self, *args, **kwargs)

        context = _TraceContext._extract(_dd_trace_ctx)
        dd_tracer.context_provider.activate(context)

        job_submission_id = os.environ.get("_RAY_SUBMISSION_ID")

        with dd_tracer.trace(
            f"{self.__class__.__name__}.{method.__name__}", service=job_submission_id, span_type=SpanTypes.RAY
        ) as span:
            span.set_tag_str(SPAN_KIND, SpanKind.CONSUMER)
            _inject_ray_span_tags(span)

            try:
                _inject_context_in_env(span.context)

                await method(self, *args, **kwargs)
            except ray.exceptions.AsyncioActorExit as e:
                # if the job succedded we removed from the span
                # the error used to exit the actor
                job_info = await self._job_info_client.get_info(job_submission_id)
                if str(job_info.status) == "FAILED":
                    raise e
            except Exception as e:
                raise e

    return _traced_run_method


@contextmanager
def _trace_actor_method(self: Any, method: Callable[..., Any], dd_trace_ctx):
    if tracer.current_span() is None:
        context = _TraceContext._extract(dd_trace_ctx)
        tracer.context_provider.activate(context)

    with tracer.trace(
        f"{self.__class__.__name__}.{method.__name__}",
        service=os.environ.get("_RAY_SUBMISSION_ID"),
        span_type=SpanTypes.RAY,
    ) as span:
        span.set_tag_str(SPAN_KIND, SpanKind.CONSUMER)
        _inject_ray_span_tags(span)

        yield span


def _inject_tracing_actor_method(method: Callable[..., Any]) -> Any:
    def _traced_method(self: Any, *args: Any, _dd_trace_ctx=None, **kwargs: Any) -> Any:
        from ddtrace import tracer

        if not tracer or (_dd_trace_ctx is None and tracer.current_span() is None):
            return method(self, *args, **kwargs)

        with _trace_actor_method(self, method, _dd_trace_ctx):
            return method(self, *args, **kwargs)

    return _traced_method


def _inject_tracing_async_actor_method(method: Callable[..., Any]) -> Any:
    async def _traced_async_method(self: Any, *args: Any, _dd_trace_ctx=None, **kwargs: Any) -> Any:
        from ddtrace import tracer

        if not tracer or (_dd_trace_ctx is None and tracer.current_span() is None):
            return await method(self, *args, **kwargs)

        with _trace_actor_method(self, method, _dd_trace_ctx):
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
            continue

        method.__signature__ = _inject_dd_trace_ctx_kwarg(method)

        # Special handling for the run method in JobSupervisor
        if is_job_supervisor and name == "run" and inspect.iscoroutinefunction(method):
            wrapped_method = wraps(method)(job_supervisor_run_wrapper(method))
        else:
            if inspect.iscoroutinefunction(method):
                wrapped_method = wraps(method)(_inject_tracing_async_actor_method(method))
            else:
                wrapped_method = wraps(method)(_inject_tracing_actor_method(method))

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


class RayLoggingFileHandler(logging.FileHandler):
    pass


def setup_logging():
    dd_trace_ray_output_log_dir = os.environ.get("DD_TRACE_RAY_OUTPUT_LOG_DIR", "/tmp/ray/session_latest/logs/")
    os.makedirs(dd_trace_ray_output_log_dir, exist_ok=True)
    dd_trace_ray_output_log_path = os.path.join(dd_trace_ray_output_log_dir, "dd-trace-ray.log")
    file_handler = RayLoggingFileHandler(dd_trace_ray_output_log_path)
    json_formatter = JsonFormatter(fmt=LOG_FORMAT)  # type: ignore
    file_handler.setFormatter(json_formatter)
    file_handler.addFilter(ActiveSpanFilter())
    logger.addHandler(file_handler)


def remove_logging():
    logger = logging.getLogger()
    logger.handlers = [h for h in logger.handlers if not isinstance(h, RayLoggingFileHandler)]


def patch():
    if getattr(ray, "_datadog_patch", False):
        return

    ray._datadog_patch = True

    tracer._span_aggregator.user_processors.append(RayTraceProcessor())

    setup_logging()

    _w(ray.remote_function.RemoteFunction, "_remote", traced_submit_task)

    _w(ray.dashboard.modules.job.job_manager.JobManager, "submit_job", traced_submit_job)
    _w(ray.dashboard.modules.job.job_manager.JobManager, "_monitor_job_internal", traced_end_job)

    _w(ray.actor, "_modify_class", inject_tracing_into_actor_class)
    _w(ray.actor.ActorHandle, "_actor_method_call", traced_actor_method_call)


def unpatch():
    if not getattr(ray, "_datadog_patch", False):
        return

    ray._datadog_patch = False

    tracer._span_aggregator.user_processors = [
        p for p in tracer._span_aggregator.user_processors if not isinstance(p, RayTraceProcessor)
    ]

    remove_logging()

    _u(ray.remote_function.RemoteFunction, "_remote")

    _u(ray.dashboard.modules.job.job_manager.JobManager, "submit_job")
    _u(ray.dashboard.modules.job.job_manager.JobManager, "_monitor_job_internal")

    _u(ray.actor, "_modify_class")
    _u(ray.actor.ActorHandle, "_actor_method_call")
