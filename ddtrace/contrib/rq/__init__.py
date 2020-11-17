"""
The RQ integration will trace your jobs.

Usage
~~~~~

The rq integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`patch_all()<patch_all>`.

Or use :ref:`patch()<patch>` to manually enable the integration::

    from ddtrace import patch
    patch(rq=True)


Worker Usage
~~~~~~~~~~~~

``ddtrace-run`` can be used to easily trace your workers::

    DD_RQ_WORKER_SERVICE=myworker ddtrace-run rq worker


See https://github.com/DataDog/trace-examples/tree/kyle-verhoog/rq/python/rq
for a working example.


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~



Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.rq['distributed_tracing_enabled']

   If ``True`` the integration will connect the traces sent between the queuer
   and the RQ worker.

   Default: ``False``

.. py:data:: ddtrace.config.rq['service']

   The service name reported by default for RQ spans from the app.

   This option can also be set with the ``DD_RQ_SERVICE`` or ``DD_SERVICE``
   environment variables.

   Default: ``rq``

.. py:data:: ddtrace.config.rq_worker['service']

   The service name reported by default for RQ spans from workers.

   This option can also be set with the ``DD_RQ_WORKER_SERVICE`` environment
   variable.

   Default: ``rq-worker``
"""
from ddtrace import config, Pin
from ...ext import SpanTypes
from ...propagation.http import HTTPPropagator
from .. import trace_utils


__all__ = [
    "patch",
    "unpatch",
]


config._add(
    "rq",
    dict(
        _default_service="rq",
        distributed_tracing_enabled=False,
    ),
)

config._add(
    "rq_worker",
    dict(
        _default_service="rq-worker",
    ),
)


propagator = HTTPPropagator()


@trace_utils.with_traced_module
def traced_queue_enqueue_job(rq, pin, func, instance, args, kwargs):
    job = args[0]

    with pin.tracer.trace(
        "rq.queue.enqueue_job",
        service=trace_utils.int_service(pin, config.rq),
        span_type=SpanTypes.WORKER,
        resource=job.func_name,
    ) as span:
        span.set_tag("queue.name", instance.name)
        span.set_tag("job.id", job.get_id())
        span.set_tag("job.func_name", job.func_name)

        # If the queue is_async then add distributed tracing headers to the job
        if instance.is_async and config.rq.distributed_tracing_enabled:
            propagator.inject(span.context, job.meta)

        try:
            return func(*args, **kwargs)
        finally:
            # If the queue is not async then the job is run immediately so we can
            # report the results
            if not instance.is_async:
                span.set_tag("job.status", job.get_status())


@trace_utils.with_traced_module
def traced_queue_fetch_job(rq, pin, func, instance, args, kwargs):
    with pin.tracer.trace("rq.queue.fetch_job", service=trace_utils.int_service(pin, config.rq)) as span:
        span.set_tag("job.id", args[0])
        return func(*args, **kwargs)


@trace_utils.with_traced_module
def traced_perform_job(rq, pin, func, instance, args, kwargs):
    """Trace rq.Worker.perform_job"""
    # `perform_job` is executed in a freshly forked, short-lived instance
    job = args[0]

    ctx = propagator.extract(job.meta)
    if ctx.trace_id:
        pin.tracer.context_provider.activate(ctx)

    try:
        with pin.tracer.trace(
            "rq.worker.perform_job",
            service=trace_utils.ext_service(pin, config.rq_worker),
            span_type=SpanTypes.WORKER,
            resource=job.func_name,
        ) as span:
            span.set_tag("job.id", job.get_id())
            return func(*args, **kwargs)
    finally:
        if job.get_status() == rq.job.JobStatus.FAILED:
            span.error = 1
            span.set_tag("error.stack", job.exc_info)
        span.set_tag("status", job.get_status())
        span.set_tag("origin", job.origin)

        # Force flush to agent since the process `os.exit()`s
        # immediately after this method returns
        pin.tracer.writer.flush_queue()


@trace_utils.with_traced_module
def traced_job_perform(rq, pin, func, instance, args, kwargs):
    """Trace rq.Job.perform(...)"""
    job = instance

    # Inherit the service name from whatever parent exists.
    # eg. in a worker, a perform_job parent span will exist with the worker
    #     service.
    with pin.tracer.trace("rq.job.perform", resource=job.func_name) as span:
        span.set_tag("job.id", job.get_id())
        return func(*args, **kwargs)


@trace_utils.with_traced_module
def traced_job_fetch(rq, pin, func, instance, args, kwargs):
    """Trace rq.Job.fetch(...)"""

    job = None
    try:
        with pin.tracer.trace("rq.job.fetch", service=trace_utils.ext_service(pin, config.rq_worker)) as span:
            span.set_tag("job.id", args[0])
            job = func(*args, **kwargs)
            return job
    finally:
        if job:
            job_status = job.get_status()
            span.set_tag("job.status", job_status)


@trace_utils.with_traced_module
def traced_job_fetch_many(rq, pin, func, instance, args, kwargs):
    """Trace rq.Job.fetch_many(...)"""
    with pin.tracer.trace("rq.job.fetch_many", service=trace_utils.ext_service(pin, config.rq_worker)) as span:
        span.set_tag("job_ids", args[0])
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
    trace_utils.wrap(rq.job, "Job.fetch", traced_job_fetch(rq.job.Job))

    # Patch rq.queue.Queue
    Pin().onto(rq.queue.Queue)
    trace_utils.wrap("rq.queue", "Queue.enqueue_job", traced_queue_enqueue_job(rq))
    trace_utils.wrap("rq.queue", "Queue.fetch_job", traced_queue_fetch_job(rq))

    # Patch rq.worker.Worker
    Pin().onto(rq.worker.Worker)
    trace_utils.wrap(rq.worker, "Worker.perform_job", traced_perform_job(rq))

    setattr(rq, "_datadog_patch", True)


def unpatch():
    import rq

    if not getattr(rq, "_datadog_patch", False):
        return

    Pin.remove_from(rq)

    # Unpatch rq.job.Job
    Pin.remove_from(rq.job.Job)
    trace_utils.unwrap(rq.job.Job, "perform")
    trace_utils.unwrap(rq.job.Job, "fetch")

    # Unpatch rq.queue.Queue
    Pin.remove_from(rq.queue.Queue)
    trace_utils.unwrap(rq.queue.Queue, "enqueue_job")
    trace_utils.unwrap(rq.queue.Queue, "fetch_job")

    # Unpatch rq.worker.Worker
    Pin.remove_from(rq.worker.Worker)
    trace_utils.unwrap(rq.worker.Worker, "perform_job")

    setattr(rq, "_datadog_patch", False)
