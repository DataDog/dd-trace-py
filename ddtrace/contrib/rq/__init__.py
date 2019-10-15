"""
TODO
"""
import sys

from ddtrace.vendor.wrapt import wrap_function_wrapper as _w
from ddtrace import config, Pin
from ...ext import AppTypes
from ...propagation.http import HTTPPropagator
from ...utils.import_hook import install_module_import_hook, uninstall_module_import_hook, module_patched, _mark_module_patched
from ...utils.wrappers import unwrap as _uw


__all__ = [
    'patch',
    'patch_job',
    'unpatch',
]


config._add('rq', dict(
    service_name='rq',
    worker_service_name='rq-worker',
    app='rq',
    app_type=AppTypes.worker,
    distributed_tracing_enabled=True,
))


def with_instance_pin(func):
    """Helper to wrap a function wrapper and ensure an enabled pin is available for the `instance`"""
    def with_cls(cls):
        def wrapper(wrapped, instance, args, kwargs):
            import rq
            pin = Pin._find(wrapped, instance, cls, rq)
            if not pin or not pin.enabled():
                return wrapped(*args, **kwargs)
            return func(rq, pin, wrapped, instance, args, kwargs)
        return wrapper
    return with_cls


def unpatch():
    if 'rq' in sys.modules:
        import rq
        _uw(rq.job.Job, 'fetch')
        _uw(rq.job.Job, 'perform')
        _uw(rq.queue.Queue, 'enqueue_job')
        _uw(rq.queue.Queue, 'fetch_job')
        _uw(rq.worker.Worker, 'perform_job')
    uninstall_module_import_hook('rq')
    uninstall_module_import_hook('rq.job')
    uninstall_module_import_hook('rq.queue')
    uninstall_module_import_hook('rq.worker')


def trace_init(rq):
    """Install a pin on the rq module to fallback on if no pins are found.
    """
    Pin(service=config.rq['service_name'], app=config.rq['app'], app_type=config.rq['app_type']).onto(rq)


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


def patch_job(rq_job):
    Pin(service=config.rq['worker_service_name'], app=config.rq['app'], app_type=config.rq['app_type']).onto(rq_job.Job)
    _w(rq_job, 'Job.perform', traced_job_perform(rq_job.Job))
    _w(rq_job, 'Job.fetch', traced_job_fetch(rq_job.Job))


def patch_queue(rq_queue):
    Pin(service=config.rq['service_name'], app=config.rq['app'], app_type=config.rq['app_type']).onto(rq_queue.Queue)
    _w('rq.queue', 'Queue.enqueue_job', traced_queue_enqueue_job(rq_queue.Queue))
    _w('rq.queue', 'Queue.fetch_job', traced_queue_fetch_job(rq_queue.Queue))


def patch_worker(rq_worker):
    Pin(service=config.rq['worker_service_name'], app=config.rq['app'], app_type=config.rq['app_type']).onto(rq_worker.Worker)
    _w(rq_worker, 'Worker.perform_job', traced_perform_job(rq_worker.Worker))


def patch():
    if 'rq' not in sys.modules:
        install_module_import_hook('rq', trace_init)
        install_module_import_hook('rq.job', patch_job)
        install_module_import_hook('rq.queue', patch_queue)
        install_module_import_hook('rq.worker', patch_worker)
    else:
        import rq

        if not module_patched(rq):
            _mark_module_patched(rq)
            trace_init(rq)
            patch_job(rq.job)
            patch_queue(rq.queue)
            patch_worker(rq.worker)
