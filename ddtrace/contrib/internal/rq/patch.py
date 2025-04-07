from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.trace import Pin

from ....ext import SpanKind
from ....ext import SpanTypes
from ....settings._config import _get_config
from ... import trace_utils


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


def get_version():
    # type: () -> str
    import rq

    return str(getattr(rq, "__version__", ""))


@trace_utils.with_traced_module
def traced_queue_enqueue_job(rq, pin, func, instance, args, kwargs):
    job = get_argument_value(args, kwargs, 0, "f")

    func_name = job.func_name
    job_inst = job.instance
    job_inst_str = "%s.%s" % (job_inst.__module__, job_inst.__class__.__name__) if job_inst else ""

    if job_inst_str:
        resource = "%s.%s" % (job_inst_str, func_name)
    else:
        resource = func_name

    with core.context_with_data(
        "rq.queue.enqueue_job",
        span_name=schematize_messaging_operation(
            "rq.queue.enqueue_job", provider="rq", direction=SpanDirection.OUTBOUND
        ),
        pin=pin,
        service=trace_utils.int_service(pin, config.rq),
        resource=resource,
        span_type=SpanTypes.WORKER,
        integration_config=config.rq_worker,
        tags={
            COMPONENT: config.rq.integration_name,
            SPAN_KIND: SpanKind.PRODUCER,
            QUEUE_NAME: instance.name,
            JOB_ID: job.get_id(),
            JOB_FUNC_NAME: job.func_name,
        },
    ) as ctx, ctx.span:
        # If the queue is_async then add distributed tracing headers to the job
        if instance.is_async:
            core.dispatch("rq.queue.enqueue_job", [ctx, job.meta])
        return func(*args, **kwargs)


@trace_utils.with_traced_module
def traced_queue_fetch_job(rq, pin, func, instance, args, kwargs):
    job_id = get_argument_value(args, kwargs, 0, "job_id")
    with core.context_with_data(
        "rq.traced_queue_fetch_job",
        span_name=schematize_messaging_operation(
            "rq.queue.fetch_job", provider="rq", direction=SpanDirection.PROCESSING
        ),
        pin=pin,
        service=trace_utils.int_service(pin, config.rq),
        tags={COMPONENT: config.rq.integration_name, JOB_ID: job_id},
    ) as ctx, ctx.span:
        return func(*args, **kwargs)


@trace_utils.with_traced_module
def traced_perform_job(rq, pin, func, instance, args, kwargs):
    """Trace rq.Worker.perform_job"""
    # `perform_job` is executed in a freshly forked, short-lived instance
    job = get_argument_value(args, kwargs, 0, "job")

    try:
        with core.context_with_data(
            "rq.worker.perform_job",
            span_name="rq.worker.perform_job",
            service=trace_utils.int_service(pin, config.rq_worker),
            pin=pin,
            span_type=SpanTypes.WORKER,
            resource=job.func_name,
            integration_config=config.rq_worker,
            distributed_headers=job.meta,
            tags={COMPONENT: config.rq.integration_name, SPAN_KIND: SpanKind.CONSUMER, JOB_ID: job.get_id()},
        ) as ctx, ctx.span:
            try:
                return func(*args, **kwargs)
            finally:
                # call _after_perform_job handler for job status and origin
                span_tags = {"job.status": job.get_status() or "None", "job.origin": job.origin}
                job_failed = job.is_failed
                core.dispatch("rq.worker.perform_job", [ctx, job_failed, span_tags])

    finally:
        # Force flush to agent since the process `os.exit()`s
        # immediately after this method returns
        core.dispatch("rq.worker.after.perform.job", [ctx])


@trace_utils.with_traced_module
def traced_job_perform(rq, pin, func, instance, args, kwargs):
    """Trace rq.Job.perform(...)"""
    job = instance

    # Inherit the service name from whatever parent exists.
    # eg. in a worker, a perform_job parent span will exist with the worker
    #     service.
    with core.context_with_data(
        "rq.job.perform",
        span_name="rq.job.perform",
        resource=job.func_name,
        pin=pin,
        tags={COMPONENT: config.rq.integration_name, JOB_ID: job.get_id()},
    ) as ctx, ctx.span:
        return func(*args, **kwargs)


@trace_utils.with_traced_module
def traced_job_fetch_many(rq, pin, func, instance, args, kwargs):
    """Trace rq.Job.fetch_many(...)"""
    job_ids = get_argument_value(args, kwargs, 0, "job_ids")
    with core.context_with_data(
        "rq.job.fetch_many",
        span_name=schematize_messaging_operation(
            "rq.job.fetch_many", provider="rq", direction=SpanDirection.PROCESSING
        ),
        service=trace_utils.ext_service(pin, config.rq_worker),
        pin=pin,
        tags={COMPONENT: config.rq.integration_name, JOB_ID: job_ids},
    ) as ctx, ctx.span:
        return func(*args, **kwargs)


def patch():
    # Avoid importing rq at the module level, eventually will be an import hook
    import rq

    if getattr(rq, "_datadog_patch", False):
        return

    Pin().onto(rq)

    # Patch rq.job.Job
    Pin().onto(rq.job.Job)
    trace_utils.wrap(rq.job, "Job.perform", traced_job_perform(rq.job.Job))

    # Patch rq.queue.Queue
    Pin().onto(rq.queue.Queue)
    trace_utils.wrap("rq.queue", "Queue.enqueue_job", traced_queue_enqueue_job(rq))
    trace_utils.wrap("rq.queue", "Queue.fetch_job", traced_queue_fetch_job(rq))

    # Patch rq.worker.Worker
    Pin().onto(rq.worker.Worker)
    trace_utils.wrap(rq.worker, "Worker.perform_job", traced_perform_job(rq))

    rq._datadog_patch = True


def unpatch():
    import rq

    if not getattr(rq, "_datadog_patch", False):
        return

    Pin().remove_from(rq)

    # Unpatch rq.job.Job
    Pin().remove_from(rq.job.Job)
    trace_utils.unwrap(rq.job.Job, "perform")

    # Unpatch rq.queue.Queue
    Pin().remove_from(rq.queue.Queue)
    trace_utils.unwrap(rq.queue.Queue, "enqueue_job")
    trace_utils.unwrap(rq.queue.Queue, "fetch_job")

    # Unpatch rq.worker.Worker
    Pin().remove_from(rq.worker.Worker)
    trace_utils.unwrap(rq.worker.Worker, "perform_job")

    rq._datadog_patch = False
