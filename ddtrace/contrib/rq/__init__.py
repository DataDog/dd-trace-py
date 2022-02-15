"""
The RQ__ integration will trace your jobs.


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

    DD_SERVICE=myworker ddtrace-run rq worker



Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To override the service name for a queue::

    from ddtrace import Pin

    connection = redis.Redis()
    queue = rq.Queue(connection=connection)
    Pin.override(queue, service="custom_queue_service")


To override the service name for a particular worker::

    worker = rq.SimpleWorker([queue], connection=queue.connection)
    Pin.override(worker, service="custom_worker_service")


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.rq['distributed_tracing_enabled']
.. py:data:: ddtrace.config.rq_worker['distributed_tracing_enabled']

   If ``True`` the integration will connect the traces sent between the enqueuer
   and the RQ worker.

   This option can also be set with the ``DD_RQ_DISTRIBUTED_TRACING_ENABLED``
   environment variable on either the enqueuer or worker applications.

   Default: ``True``

.. py:data:: ddtrace.config.rq['service']

   The service name reported by default for RQ spans from the app.

   This option can also be set with the ``DD_SERVICE`` or ``DD_RQ_SERVICE``
   environment variables.

   Default: ``rq``

.. py:data:: ddtrace.config.rq_worker['service']

   The service name reported by default for RQ spans from workers.

   This option can also be set with the ``DD_SERVICE`` environment
   variable.

   Default: ``rq-worker``

.. __: https://python-rq.org/

"""
import os

from ddtrace import Pin
from ddtrace import config

from .. import trace_utils
from ...ext import SpanTypes
from ...internal.utils import get_argument_value
from ...internal.utils.formats import asbool
from ...propagation.http import HTTPPropagator


__all__ = [
    "patch",
    "unpatch",
]


config._add(
    "rq",
    dict(
        distributed_tracing_enabled=asbool(os.environ.get("DD_RQ_DISTRIBUTED_TRACING_ENABLED", True)),
        _default_service="rq",
    ),
)

config._add(
    "rq_worker",
    dict(
        distributed_tracing_enabled=asbool(os.environ.get("DD_RQ_DISTRIBUTED_TRACING_ENABLED", True)),
        _default_service="rq-worker",
    ),
)


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

    with pin.tracer.trace(
        "rq.queue.enqueue_job",
        service=trace_utils.int_service(pin, config.rq),
        resource=resource,
        span_type=SpanTypes.WORKER,
    ) as span:
        span._set_str_tag("queue.name", instance.name)
        span.set_tag("job.id", job.get_id())
        span._set_str_tag("job.func_name", job.func_name)

        # If the queue is_async then add distributed tracing headers to the job
        if instance.is_async and config.rq.distributed_tracing_enabled:
            HTTPPropagator.inject(span.context, job.meta)
        return func(*args, **kwargs)


@trace_utils.with_traced_module
def traced_queue_fetch_job(rq, pin, func, instance, args, kwargs):
    with pin.tracer.trace("rq.queue.fetch_job", service=trace_utils.int_service(pin, config.rq)) as span:
        job_id = get_argument_value(args, kwargs, 0, "job_id")
        span.set_tag("job.id", job_id)
        return func(*args, **kwargs)


@trace_utils.with_traced_module
def traced_perform_job(rq, pin, func, instance, args, kwargs):
    """Trace rq.Worker.perform_job"""
    # `perform_job` is executed in a freshly forked, short-lived instance
    job = get_argument_value(args, kwargs, 0, "job")

    if config.rq_worker.distributed_tracing_enabled:
        ctx = HTTPPropagator.extract(job.meta)
        if ctx.trace_id:
            pin.tracer.context_provider.activate(ctx)

    try:
        with pin.tracer.trace(
            "rq.worker.perform_job",
            service=trace_utils.int_service(pin, config.rq_worker),
            span_type=SpanTypes.WORKER,
            resource=job.func_name,
        ) as span:
            span.set_tag("job.id", job.get_id())
            try:
                return func(*args, **kwargs)
            finally:
                span.set_tag("job.status", job.get_status())
                span.set_tag("job.origin", job.origin)
                if job.is_failed:
                    span.error = 1
    finally:
        # Force flush to agent since the process `os.exit()`s
        # immediately after this method returns
        pin.tracer.flush()


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
def traced_job_fetch_many(rq, pin, func, instance, args, kwargs):
    """Trace rq.Job.fetch_many(...)"""
    with pin.tracer.trace("rq.job.fetch_many", service=trace_utils.ext_service(pin, config.rq_worker)) as span:
        job_ids = get_argument_value(args, kwargs, 0, "job_ids")
        span.set_tag("job_ids", job_ids)
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

    setattr(rq, "_datadog_patch", True)


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

    setattr(rq, "_datadog_patch", False)
