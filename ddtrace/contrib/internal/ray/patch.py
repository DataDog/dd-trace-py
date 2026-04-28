from collections.abc import Mapping
import json
from typing import Any

import ray
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib._events.ray import RayJobEvent
from ddtrace.contrib.internal.ray.core.actor import inject_tracing_into_actor_class
from ddtrace.contrib.internal.ray.core.actor import traced_actor_method_submission
from ddtrace.contrib.internal.ray.core.api import traced_get
from ddtrace.contrib.internal.ray.core.api import traced_put
from ddtrace.contrib.internal.ray.core.api import traced_wait
from ddtrace.contrib.internal.ray.core.remote_function import traced_submit_task
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.settings import env
from ddtrace.internal.telemetry import get_config as _get_config
from ddtrace.internal.threads import Lock
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool

from .constants import DEFAULT_JOB_NAME
from .constants import RAY_JOB_NAME
from .core.utils import get_dd_job_name_from_entrypoint
from .core.utils import redact_paths


log = get_logger(__name__)

_job_context_lock = Lock()
_job_contexts = {}


def _parse_ignored_actors(value: Any) -> dict[str, frozenset[str]]:
    if not value:
        return {}

    if isinstance(value, str):
        try:
            parsed_value = json.loads(value)
        except ValueError:
            log.warning("Invalid DD_TRACE_RAY_IGNORED_ACTORS value. Expected a JSON object.")
            return {}
    else:
        parsed_value = value

    if not isinstance(parsed_value, Mapping):
        log.warning("Invalid DD_TRACE_RAY_IGNORED_ACTORS value. Expected a JSON object.")
        return {}

    ignored_actors = {}
    for actor_name, ignored_methods in parsed_value.items():
        if not isinstance(actor_name, str) or not actor_name.strip():
            log.warning("Invalid DD_TRACE_RAY_IGNORED_ACTORS actor name. Expected a non-empty string.")
            continue

        actor_name = actor_name.strip()
        if ignored_methods == "*":
            ignored_actors[actor_name] = frozenset({"*"})
            continue

        if not isinstance(ignored_methods, (list, tuple, set, frozenset)):
            log.warning("Invalid DD_TRACE_RAY_IGNORED_ACTORS methods for actor %s. Expected a list.", actor_name)
            continue

        methods = frozenset(method.strip() for method in ignored_methods if isinstance(method, str) and method.strip())
        if methods:
            ignored_actors[actor_name] = methods

    return ignored_actors


config._add(
    "ray",
    dict(
        use_entrypoint_as_service_name=asbool(env.get("DD_TRACE_RAY_USE_ENTRYPOINT_AS_SERVICE_NAME", default=False)),
        redact_entrypoint_paths=asbool(env.get("DD_TRACE_RAY_REDACT_ENTRYPOINT_PATHS", default=True)),
        trace_core_api=_get_config("DD_TRACE_RAY_CORE_API", default=False, modifier=asbool),
        trace_args_kwargs=_get_config("DD_TRACE_RAY_ARGS_KWARGS", default=False, modifier=asbool),
        submission_spans=_get_config("DD_TRACE_RAY_SUBMISSION_SPANS_ENABLED", default=False, modifier=asbool),
        ignored_actors=_get_config("DD_TRACE_RAY_IGNORED_ACTORS", default={}, modifier=_parse_ignored_actors),
    ),
)


def _supported_versions() -> dict[str, str]:
    return {"ray": ">=2.46.0"}


def get_version() -> str:
    return str(getattr(ray, "__version__", ""))


def traced_submit_job(wrapped, instance, args, kwargs):
    """Trace job submission. This function is also responsible
    of creating the root span.
    It will also inject _RAY_SUBMISSION_ID and _RAY_JOB_NAME
    in the env variable as some spans will not have access to them
    through ray_ctx
    """
    from ray.dashboard.modules.job.job_manager import generate_job_id

    # Three ways of setting the service name of the spans, in order of precedence:
    # - DD_SERVICE environment variable
    # - The name of the entrypoint if DD_TRACE_RAY_USE_ENTRYPOINT_AS_SERVICE_NAME is True
    # - Metadata JSON: ray job submit --metadata_json '{"job_name": "train.cool.model"}'
    # Otherwise set to unnamed.ray.job
    submission_id = kwargs.get("submission_id") or generate_job_id()
    kwargs["submission_id"] = submission_id

    entrypoint = kwargs.get("entrypoint", "")
    if config.ray.redact_entrypoint_paths:
        entrypoint = redact_paths(entrypoint)

    metadata = kwargs.get("metadata", {}) or {}
    if config.ray.use_entrypoint_as_service_name:
        job_name = get_dd_job_name_from_entrypoint(entrypoint) or DEFAULT_JOB_NAME
    else:
        user_provided_service = config.service if config._is_user_provided_service else None
        metadata_job_name = metadata.get("job_name", None)
        job_name = user_provided_service or metadata_job_name or DEFAULT_JOB_NAME

    # Ensure dashboard-side submission spans can resolve the current Ray job name.
    env[RAY_JOB_NAME] = job_name

    # These dictionary are used to inject the tracing context in env variables
    # that are going to be sent to all ray workers
    runtime_env = kwargs.get("runtime_env") or {}
    kwargs["runtime_env"] = runtime_env
    env_vars = runtime_env.get("env_vars") or {}
    runtime_env["env_vars"] = env_vars

    # Align ddtrace global service in Ray runtime processes with Ray job spans.
    # This prevents inferred services (for example "ray.dashboard") from being
    # attached as _dd.base_service on worker spans.
    env_vars.setdefault("DD_SERVICE", job_name)

    with core.context_with_event(
        RayJobEvent(
            service=job_name or DEFAULT_JOB_NAME,
            component=config.ray.integration_name,
            integration_config=config.ray,
            submission_id=submission_id,
            job_name=job_name,
            entrypoint=entrypoint,
            metadata=metadata,
            environment_variables=env_vars,
        ),
        dispatch_end_event=False,
    ) as ctx:
        try:
            resp = wrapped(*args, **kwargs)
            with _job_context_lock:
                _job_contexts[submission_id] = ctx
            return resp
        except BaseException as e:
            ctx.event.submit_failed = True
            ctx.dispatch_ended_event(type(e), e, e.__traceback__)
            raise e


async def traced_end_job(wrapped, instance, args, kwargs):
    submission_id = get_argument_value(args, kwargs, 0, "job_id")
    with _job_context_lock:
        ctx = _job_contexts.pop(submission_id, None)

    if ctx is None:
        return await wrapped(*args, **kwargs)

    try:
        result = await wrapped(*args, **kwargs)

        ctx.event.ended_job_info = await instance._job_info_client.get_info(submission_id)
        ctx.dispatch_ended_event()

        return result
    except BaseException as e:
        ctx.dispatch_ended_event(type(e), e, e.__traceback__)
        raise e


def patch():
    if getattr(ray, "_datadog_patch", False):
        return

    import ddtrace._trace.subscribers.ray  # noqa: F401

    ray._datadog_patch = True

    from ray.util.tracing import tracing_helper

    # Disable Ray native tracing so Datadog remains the single tracing system and
    # avoids duplicate or conflicting spans/context propagation.
    tracing_helper._global_is_tracing_enabled = False

    @ModuleWatchdog.after_module_imported("ray.actor")
    def _(m):
        _w(m.ActorHandle, "_actor_method_call", traced_actor_method_submission)
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
