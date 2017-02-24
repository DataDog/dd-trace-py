# Third party
import wrapt

# Project
from ddtrace import Pin
from ddtrace.ext import AppTypes
from ...ext import errors
from .util import APP, SERVICE, meta_from_context, require_pin

# Task operations
TASK_APPLY = 'celery.task.apply'
TASK_APPLY_ASYNC = 'celery.task.apply_async'
TASK_RUN = 'celery.task.run'


def patch_task(task, pin=None):
    """ patch_task will add tracing to a celery task """
    pin = pin or Pin(service=SERVICE, app=APP, app_type=AppTypes.worker)

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
    with pin.tracer.trace(TASK_RUN, service=pin.service, resource=task.name) as span:
        # Set meta data from task request
        span.set_metas(meta_from_context(task.request))

        # Call original `run` function
        return func(*args, **kwargs)


@require_pin
def _task_apply(pin, func, task, args, kwargs):
    with pin.tracer.trace(TASK_APPLY, service=pin.service, resource=task.name) as span:
        # Call the original `apply` function
        res = func(*args, **kwargs)

        # Set meta data from response
        span.set_meta('id', res.id)
        span.set_meta('state', res.state)
        if res.traceback:
            span.error = 1
            span.set_meta(errors.STACK, res.traceback)
        return res


@require_pin
def _task_apply_async(pin, func, task, args, kwargs):
    with pin.tracer.trace(TASK_APPLY_ASYNC, service=pin.service, resource=task.name) as span:
        # Extract meta data from `kwargs`
        meta_keys = (
            'compression', 'countdown', 'eta', 'exchange', 'expires',
            'priority', 'routing_key', 'serializer', 'queue',
        )
        for name in meta_keys:
            if name in kwargs:
                span.set_meta(name, kwargs[name])

        # Call the original `apply_async` function
        res = func(*args, **kwargs)

        # Set meta data from response
        # DEV: Calling `res.traceback` or `res.state` will make an
        #   API call to the backend for the properties
        span.set_meta('id', res.id)
        return res
