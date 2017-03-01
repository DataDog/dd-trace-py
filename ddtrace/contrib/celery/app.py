# Standard library
import types

# Third party
import wrapt

# Project
from ddtrace import Pin
from ddtrace.ext import AppTypes
from .task import patch_task, unpatch_task
from .util import APP, SERVICE, require_pin


def patch_app(app, pin=None):
    """ patch_app will add tracing to a celery app """
    pin = pin or Pin(service=SERVICE, app=APP, app_type=AppTypes.worker)
    patch_methods = [
        ('task', _app_task),
    ]
    for method_name, wrapper in patch_methods:
        # Get the original method
        method = getattr(app, method_name, None)
        if method is None:
            continue

        # Do not patch if method is already patched
        if isinstance(method, wrapt.ObjectProxy):
            continue

        # Patch method
        setattr(app, method_name, wrapt.FunctionWrapper(method, wrapper))

    # patch the Task class if available
    setattr(app, 'Task', patch_task(app.Task))

    # Attach our pin to the app
    pin.onto(app)
    return app


def unpatch_app(app):
    """ unpatch_app will remove tracing from a celery app """
    patched_methods = [
        'task',
    ]
    for method_name in patched_methods:
        # Get the wrapped method
        wrapper = getattr(app, method_name, None)
        if wrapper is None:
            continue

        # Only unpatch if the wrapper is an `ObjectProxy`
        if not isinstance(wrapper, wrapt.ObjectProxy):
            continue

        # Restore original method
        setattr(app, method_name, wrapper.__wrapped__)

    # restore the original Task class
    setattr(app, 'Task', unpatch_task(app.Task))
    return app


@require_pin
def _app_task(pin, func, app, args, kwargs):
    task = func(*args, **kwargs)

    # `app.task` is a decorator which may return a function wrapper
    if isinstance(task, types.FunctionType):
        def wrapper(func, instance, args, kwargs):
            return patch_task(func(*args, **kwargs), pin=pin)
        return wrapt.FunctionWrapper(task, wrapper)

    return patch_task(task, pin=pin)
