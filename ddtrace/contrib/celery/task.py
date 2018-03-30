# Third party
import wrapt

# Project
from ddtrace import Pin
from ddtrace.ext import AppTypes
from ...ext import errors
from .util import APP, PRODUCER_SERVICE, WORKER_SERVICE, meta_from_context, require_pin

PRODUCER_ROOT_SPAN = 'celery.apply'
WORKER_ROOT_SPAN = 'celery.run'
# Task operations
TASK_TAG_KEY = 'celery.action'
TASK_APPLY = 'apply'
TASK_APPLY_ASYNC = 'apply_async'
TASK_RUN = 'run'


def patch_task(task, pin=None):
    """ patch_task will add tracing to a celery task """
    # The service set here is actually ignored, because it's not possible to
    # be certain whether this process is being used as a worker, a producer,
    # or both. So the service as recorded in traces is set based on the actual
    # work being done (ie. apply/apply_async vs run).
    pin = pin or Pin(service=WORKER_SERVICE, app=APP, app_type=AppTypes.worker)

    patch_methods = [
        ('__init__', _task_init),
        ('run', _task_run),
        ('apply', _task_apply),
        ('apply_async', _task_apply_async),
    ]
    for method_name, wrapper in patch_methods:
        # Get original method
        method = getattr(task, method_name, None)
        if method is None:
            continue

        # Do not patch if method is already patched
        if isinstance(method, wrapt.ObjectProxy):
            continue

        # Patch method
        # DEV: Using `BoundFunctionWrapper` ensures our `task` wrapper parameter is properly set
        setattr(task, method_name, wrapt.BoundFunctionWrapper(method, task, wrapper))

    # Attach our pin to the app
    pin.onto(task)
    return task


def unpatch_task(task):
    """ unpatch_task will remove tracing from a celery task """
    patched_methods = [
        '__init__',
        'run',
        'apply',
        'apply_async',
    ]
    for method_name in patched_methods:
        # Get wrapped method
        wrapper = getattr(task, method_name, None)
        if wrapper is None:
            continue

        # Only unpatch if wrapper is an `ObjectProxy`
        if not isinstance(wrapper, wrapt.ObjectProxy):
            continue

        # Restore original method
        setattr(task, method_name, wrapper.__wrapped__)

    return task


def _task_init(func, task, args, kwargs):
    func(*args, **kwargs)

    # Patch this task if our pin is enabled
    pin = Pin.get_from(task)
    if pin and pin.enabled():
        patch_task(task, pin=pin)


@require_pin
def _task_run(pin, func, task, args, kwargs):
    with pin.tracer.trace(WORKER_ROOT_SPAN, service=WORKER_SERVICE, resource=task.name) as span:
        # Set meta data from task request
        span.set_metas(meta_from_context(task.request))
        span.set_meta(TASK_TAG_KEY, TASK_RUN)

        # Call original `run` function
        return func(*args, **kwargs)


@require_pin
def _task_apply(pin, func, task, args, kwargs):
    with pin.tracer.trace(PRODUCER_ROOT_SPAN, service=PRODUCER_SERVICE, resource=task.name) as span:
        # Call the original `apply` function
        res = func(*args, **kwargs)

        # Set meta data from response
        span.set_meta('id', res.id)
        span.set_meta('state', res.state)
        span.set_meta(TASK_TAG_KEY, TASK_APPLY)
        if res.traceback:
            span.error = 1
            span.set_meta(errors.STACK, res.traceback)
        return res


@require_pin
def _task_apply_async(pin, func, task, args, kwargs):
    with pin.tracer.trace(PRODUCER_ROOT_SPAN, service=PRODUCER_SERVICE, resource=task.name) as span:
        # Extract meta data from `kwargs`
        meta_keys = (
            'compression', 'countdown', 'eta', 'exchange', 'expires',
            'priority', 'routing_key', 'serializer', 'queue',
        )
        for name in meta_keys:
            if name in kwargs:
                span.set_meta(name, kwargs[name])
        span.set_meta(TASK_TAG_KEY, TASK_APPLY_ASYNC)

        # Call the original `apply_async` function
        res = func(*args, **kwargs)

        # Set meta data from response
        # DEV: Calling `res.traceback` or `res.state` will make an
        #   API call to the backend for the properties
        span.set_meta('id', res.id)
        return res
