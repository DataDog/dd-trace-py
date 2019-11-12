"""
The RQ integration will trace your jobs.

Worker Usage
~~~~~~~~~~~~

``ddtrace-run`` can be used to easily trace your workers::

    DD_WORKER_SERVICE_NAME=myworker ddtrace-run rq worker


See https://github.com/DataDog/trace-examples/tree/kyle-verhoog/rq/python/rq for a working example.


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.rq['distributed_tracing_enabled']

   If ``True`` the integration will connect the traces sent between the queuer and the RQ worker.

   Default: ``False``

.. py:data:: ddtrace.config.rq['service_name'] # Or via DD_RQ_SERVICE_NAME environment variable

   The service name to be used for your RQ app.

   Default: ``rq``

.. py:data:: ddtrace.config.rq['worker_service_name'] # Or via DD_RQ_WORKER_SERVICE_NAME environment variable

   The service name to be used for your RQ worker.

   Default: ``rq-worker``
"""
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w
from ddtrace.utils.wrappers import unwrap as _uw
from ddtrace import config, Pin
from ...ext import AppTypes
from ...propagation.http import HTTPPropagator
from ...utils.formats import get_env


__all__ = [
    'patch',
    'unpatch',
]


config._add('rq', dict(
    service_name=get_env('rq', 'service_name', default='rq'),
    worker_service_name=get_env('rq', 'worker_service_name', default='rq-worker'),
    app='rq',
    app_type=AppTypes.worker,
    distributed_tracing_enabled=False,
))


def with_instance_pin(func):
    """Helper to wrap a function wrapper and ensure an enabled pin is available for the `instance`"""
    def with_cls(cls):
        def wrapper(wrapped, instance, args, kwargs):
            # DEV: this will be replaced with the module given when patching on import
            import rq

            # DEV: precedence when finding Pin is instance, class, rq module in that order
            pin = Pin._find(instance, cls, rq)
            if not pin or not pin.enabled():
                return wrapped(*args, **kwargs)
            return func(rq, pin, wrapped, instance, args, kwargs)
        return wrapper
    return with_cls


propagator = HTTPPropagator()


@with_instance_pin
def traced_queue_enqueue_job(rq, pin, func, instance, args, kwargs):
    job = args[0]

    with pin.tracer.trace('rq.queue.enqueue_job', service=pin.service, resource=job.func_name) as span:
        span.set_tag('queue.name', instance.name)
        span.set_tag('job.id', job.get_id())
        span.set_tag('job.func_name', job.func_name)

        # If the queue is_async then add distributed tracing headers to the job
        if instance.is_async and config.rq['distributed_tracing_enabled']:
            propagator.inject(span.context, job.meta)

        try:
            return func(*args, **kwargs)
        finally:
            # If the queue is not async then the job is run immediately so we can
            # report the results
            if not instance.is_async:
                span.set_tag('job.status', job.get_status())


@with_instance_pin
def traced_queue_fetch_job(rq, pin, func, instance, args, kwargs):
    with pin.tracer.trace('rq.queue.fetch_job', service=pin.service) as span:
        span.set_tag('job.id', args[0])
        return func(*args, **kwargs)


@with_instance_pin
def traced_perform_job(rq, pin, func, instance, args, kwargs):
    """Trace rq.Worker.perform_job
    """
    # `perform_job` is executed in a freshly forked, short-lived instance
    job = args[0]

    ctx = propagator.extract(job.meta)
    if ctx.trace_id:
        pin.tracer.context_provider.activate(ctx)

    try:
        with pin.tracer.trace('rq.worker.perform_job', service=pin.service, resource=job.func_name) as span:
            span.set_tag('job.id', job.get_id())
            return func(*args, **kwargs)
    finally:
        if job.get_status() == rq.job.JobStatus.FAILED:
            span.error = 1
            span.set_tag('exc_info', job.exc_info)
        span.set_tag('status', job.get_status())
        span.set_tag('origin', job.origin)

        # DEV: force flush to agent since the process `os.exit()`s
        #      immediately after this method returns
        pin.tracer.writer.stop()
        pin.tracer.writer.flush_queue()


@with_instance_pin
def traced_job_perform(rq, pin, func, instance, args, kwargs):
    """Trace rq.Job.perform(...)
    """
    job = instance

    with pin.tracer.trace('rq.job.perform', service=pin.service, resource=job.func_name) as span:
        span.set_tag('job.id', job.get_id())
        return func(*args, **kwargs)


@with_instance_pin
def traced_job_fetch(rq, pin, func, instance, args, kwargs):
    """Trace rq.Job.fetch(...)
    """

    job = None
    try:
        with pin.tracer.trace('rq.job.fetch', service=pin.service) as span:
            span.set_tag('job.id', args[0])
            job = func(*args, **kwargs)
            return job
    finally:
        if job:
            job_status = job.get_status()
            span.set_tag('job.status', job_status)


@with_instance_pin
def traced_job_fetch_many(rq, pin, func, instance, args, kwargs):
    """Trace rq.Job.fetch_many(...)
    """
    with pin.tracer.trace('rq.job.fetch_many', service=pin.service) as span:
        span.set_tag('job_ids', args[0])
        return func(*args, **kwargs)


def patch():
    # Avoid importing rq at the module level, eventually will be an import hook
    import rq

    if getattr(rq, '_datadog_patch', False):
        return

    Pin(
        service=config.rq['service_name'],
        app=config.rq['app'],
        app_type=config.rq['app_type'],
    ).onto(rq)

    # Patch rq.job.Job
    Pin(
        service=config.rq['worker_service_name'],
        app=config.rq['app'],
        app_type=config.rq['app_type'],
    ).onto(rq.job.Job)
    _w(rq.job, 'Job.perform', traced_job_perform(rq.job.Job))
    _w(rq.job, 'Job.fetch', traced_job_fetch(rq.job.Job))

    # Patch rq.queue.Queue
    Pin(
        service=config.rq['service_name'],
        app=config.rq['app'],
        app_type=config.rq['app_type'],
    ).onto(rq.queue.Queue)
    _w('rq.queue', 'Queue.enqueue_job', traced_queue_enqueue_job(rq.queue.Queue))
    _w('rq.queue', 'Queue.fetch_job', traced_queue_fetch_job(rq.queue.Queue))

    # Patch rq.worker.Worker
    Pin(
        service=config.rq['worker_service_name'],
        app=config.rq['app'],
        app_type=config.rq['app_type'],
    ).onto(rq.worker.Worker)
    _w(rq.worker, 'Worker.perform_job', traced_perform_job(rq.worker.Worker))

    setattr(rq, '_datadog_patch', True)


def unpatch():
    import rq

    if not getattr(rq, '_datadog_patch', False):
        return

    Pin.remove_from(rq)

    # Unpatch rq.job.Job
    Pin.remove_from(rq.job.Job)
    _uw(rq.job.Job, 'perform')
    _uw(rq.job.Job, 'fetch')

    # Unpatch rq.queue.Queue
    Pin.remove_from(rq.queue.Queue)
    _uw(rq.queue.Queue, 'enqueue_job')
    _uw(rq.queue.Queue, 'fetch_job')

    # Unpatch rq.worker.Worker
    Pin.remove_from(rq.worker.Worker)
    _uw(rq.worker.Worker, 'perform_job')

    setattr(rq, '_datadog_patch', False)
