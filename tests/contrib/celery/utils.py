import wrapt

from ddtrace.contrib.celery import patch_task


def patch_task_with_pin(pin=None):
    """ patch_task_with_pin can be used as a decorator for v1 Celery tasks when specifying a pin is needed"""
    @wrapt.decorator
    def wrapper(wrapped, instance, args, kwargs):
        patch_task(wrapped, pin)
        return wrapped(*args, **kwargs)
    return wrapper
