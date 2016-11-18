# Project
from ddtrace import Pin


def meta_from_context(context):
    meta_keys = (
        'called_directly', 'correlation_id', 'delivery_info', 'eta', 'expires', 'hostname',
        'id', 'is_eager', 'reply_to', 'retries', 'task', 'timelimit', 'utc',
    )

    return dict(
        (name, context.get(name)) for name in meta_keys
    )


def require_pin(decorated):
    """ decorator for extracting the `Pin` from a wrapped method """
    def wrapper(wrapped, instance, args, kwargs):
        pin = Pin.get_from(instance)
        # Execute the original method if pin is not enabled
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)

        # Execute our decorated function
        return decorated(pin, wrapped, instance, args, kwargs)
    return wrapper
