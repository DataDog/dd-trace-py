from typing import Any
from typing import Callable

from ddtrace import config
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.messaging import MessagingProcessEvent
from ddtrace.contrib._events.messaging import MessagingProducerEvent
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.settings._config import _get_config
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.trace import tracer


config._add(
    "rq",
    dict(
        distributed_tracing_enabled=asbool(_get_config("DD_RQ_DISTRIBUTED_TRACING_ENABLED", None)),
        _default_service=schematize_service_name("rq"),
    ),
)

config._add(
    "rq_worker",
    dict(
        distributed_tracing_enabled=asbool(_get_config("DD_RQ_DISTRIBUTED_TRACING_ENABLED", None)),
        _default_service=schematize_service_name("rq-worker"),
    ),
)


JOB_ID = "job.id"
QUEUE_NAME = "queue.name"
JOB_FUNC_NAME = "job.func_name"


def get_version() -> str:
    import rq

    return str(getattr(rq, "__version__", ""))


def _supported_versions() -> dict[str, str]:
    return {"rq": ">=1.8"}


def traced_queue_enqueue_job(
    func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    job = get_argument_value(args, kwargs, 0, "f")

    func_name = job.func_name
    job_inst = job.instance
    job_inst_str = "%s.%s" % (job_inst.__module__, job_inst.__class__.__name__) if job_inst else ""

    if job_inst_str:
        resource = "%s.%s" % (job_inst_str, func_name)
    else:
        resource = func_name

    event = MessagingProducerEvent(
        operation=schematize_messaging_operation(
            "rq.queue.enqueue_job", provider="rq", direction=SpanDirection.OUTBOUND
        ),
        distributed_headers=job.meta if instance.is_async else None,
        component=config.rq.integration_name,
        integration_config=config.rq,
        service=trace_utils.int_service(None, config.rq),
        resource=resource,
        measured=False,
        tags={
            QUEUE_NAME: instance.name,
            JOB_ID: job.id,
            JOB_FUNC_NAME: job.func_name,
        },
    )

    with core.context_with_event(event):
        return func(*args, **kwargs)


def traced_queue_fetch_job(
    func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    job_id = get_argument_value(args, kwargs, 0, "job_id")
    with (
        core.context_with_data(
            "rq.traced_queue_fetch_job",
            span_name=schematize_messaging_operation(
                "rq.queue.fetch_job", provider="rq", direction=SpanDirection.PROCESSING
            ),
            service=trace_utils.int_service(None, config.rq),
            tags={COMPONENT: config.rq.integration_name, JOB_ID: job_id},
            integration_config=config.rq,
        ) as ctx,
        span_from_context(ctx),
    ):
        return func(*args, **kwargs)


def traced_perform_job(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Trace rq.Worker.perform_job"""
    # `perform_job` is executed in a freshly forked, short-lived instance
    job = get_argument_value(args, kwargs, 0, "job")

    event = MessagingProcessEvent(
        operation="rq.worker.perform_job",
        distributed_headers=job.meta,
        component=config.rq.integration_name,
        integration_config=config.rq_worker,
        service=trace_utils.int_service(None, config.rq_worker),
        resource=job.func_name,
        measured=False,
        tags={JOB_ID: job.id},
    )

    try:
        with core.context_with_event(event):
            try:
                return func(*args, **kwargs)
            finally:
                # In RQ 2.x, get_status() raises InvalidJobOperation when the
                # job key no longer exists in Redis (e.g. result_ttl=0).
                # is_failed calls get_status() internally, so it can raise too.
                try:
                    status = job.get_status()
                except Exception:
                    status = None
                try:
                    event.failed = job.is_failed
                except Exception:
                    event.failed = False
                event.result_tags["job.status"] = status or "None"
                event.result_tags["job.origin"] = job.origin

    finally:
        # Force flush to agent since the process `os.exit()`s
        # immediately after this method returns
        tracer.flush()


def traced_job_perform(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Trace rq.Job.perform(...)"""
    job = instance

    # Inherit the service name from whatever parent exists.
    # eg. in a worker, a perform_job parent span will exist with the worker
    #     service.
    with (
        core.context_with_data(
            "rq.job.perform",
            span_name="rq.job.perform",
            resource=job.func_name,
            tags={COMPONENT: config.rq.integration_name, JOB_ID: job.id},
            integration_config=config.rq,
        ) as ctx,
        span_from_context(ctx),
    ):
        return func(*args, **kwargs)


def traced_job_fetch_many(
    func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    """Trace rq.Job.fetch_many(...)"""
    job_ids = get_argument_value(args, kwargs, 0, "job_ids")
    with (
        core.context_with_data(
            "rq.job.fetch_many",
            span_name=schematize_messaging_operation(
                "rq.job.fetch_many", provider="rq", direction=SpanDirection.PROCESSING
            ),
            service=trace_utils.ext_service(None, config.rq_worker),
            tags={COMPONENT: config.rq.integration_name, JOB_ID: job_ids},
            integration_config=config.rq_worker,
        ) as ctx,
        span_from_context(ctx),
    ):
        return func(*args, **kwargs)


def _worker_perform_job_owner(rq):
    """Return the common worker class that implements perform_job."""
    base_worker = getattr(rq.worker, "BaseWorker", None)
    if base_worker is not None and hasattr(base_worker, "perform_job"):
        return base_worker
    return rq.worker.Worker


def patch():
    # Avoid importing rq at the module level, eventually will be an import hook
    import rq

    if getattr(rq, "_datadog_patch", False):
        return

    trace_utils.wrap(rq.job, "Job.perform", traced_job_perform)
    trace_utils.wrap("rq.queue", "Queue.enqueue_job", traced_queue_enqueue_job)
    trace_utils.wrap("rq.queue", "Queue.fetch_job", traced_queue_fetch_job)
    trace_utils.wrap(_worker_perform_job_owner(rq), "perform_job", traced_perform_job)

    rq._datadog_patch = True


def unpatch():
    import rq

    if not getattr(rq, "_datadog_patch", False):
        return

    trace_utils.unwrap(rq.job.Job, "perform")
    trace_utils.unwrap(rq.queue.Queue, "enqueue_job")
    trace_utils.unwrap(rq.queue.Queue, "fetch_job")
    trace_utils.unwrap(_worker_perform_job_owner(rq), "perform_job")

    rq._datadog_patch = False
