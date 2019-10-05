"""
TODO
"""
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w
from ddtrace.compat import string_type
from ddtrace import config, Pin, Span
from ...ext import AppTypes
from ...propagation.http import HTTPPropagator
from ...utils.import_hook import install_module_import_hook


__all__ = [
    'patch',
]


config._add('rq', dict(
    service_name='rq',
    app='rq',
    app_type=AppTypes.web,
    distributed_tracing_enabled=True,
))


def with_instance_pin(func):
    """Helper to wrap a function wrapper and ensure an enabled pin is available for the `instance`"""
    def wrapper(wrapped, instance, args, kwargs):
        import rq  # performance impact of this?

        pin = Pin._find(wrapped, instance, rq)
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)

        return func(pin, wrapped, instance, args, kwargs)
    return wrapper


def unpatch():
    pass


def _trace_init(rq):
    """Install a pin on the rq module to fallback on if no pins are found.
    """
    Pin(service=config.rq['service_name'], app=config.rq['app'], app_type=config.rq['app_type']).onto(rq)


propagator = HTTPPropagator()


@with_instance_pin
def traced_queue_enqueue(pin, func, instance, args, kwargs):
    """Trace an rq.Queue.enqueue call.
    """
    # According to rq `f` can be:
    #  * A reference to a function
    #  * A reference to an object's instance method
    #  * A string, representing the location of a function (must be
    #    meaningful to the import context of the workers)
    f = args[0]
    if isinstance(f, string_type):
        job_name = f
    elif hasattr(f, '__name__'):
        job_name = getattr(f, '__name__')
    else:
        job_name = 'job'

    with pin.tracer.trace('rq.enqueue', service=pin.service, resource=job_name) as span:
        span.set_tag('queue', instance.name)

        # Add non-None tags
        for tag_key, kwarg_key in (('description', 'description'), ('timeout', 'timeout'), ('job_id', 'job_id')):
            val = kwargs.get(kwarg_key, None)
            if val is not None:
                span.set_tag(tag_key, val)

        # If the queue is_async then add distributed tracing headers
        if instance.is_async and config.rq['distributed_tracing_enabled']:
            meta = kwargs.get('meta', {})
            propagator.inject(span.context, meta)
            kwargs['meta'] = meta


        return func(*args, **kwargs)


@with_instance_pin
def traced_queue_enqueue_job(pin, func, instance, args, kwargs):
    # TODO
    pass


def _patch_queue(rq_queue):
    _w('rq.queue', 'Queue.enqueue', traced_queue_enqueue)


@with_instance_pin
def traced_job_result(pin, func, job, args, kwargs):
    """Trace a job result.
    """
    #context = propagator.extract(job.meta)
    #if context.trace_id:
    #    pin.tracer.context_provider.activate(context)

    # Create a span representing the job computation
    status = job.get_status()
    start_time = job.started_at
    span = Span(start=start_time)
    if status == 'finished':
        pass
    elif status == 'failed':
        pass

    return func(*args, **kwargs)


def _patch_job(rq_job):
    # TODO: wrapt doesn't seem to support @property :/
    _w(rq_job, 'Job.result', traced_job_result)


def patch():
    install_module_import_hook('rq', _trace_init)
    install_module_import_hook('rq.queue', _patch_queue)
    install_module_import_hook('rq.job', _patch_job)
