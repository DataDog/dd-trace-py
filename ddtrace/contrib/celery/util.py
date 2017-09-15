# stdlib
import os

# Project
from ddtrace import Pin

# Service info
APP = 'celery'
SERVICE = os.environ.get('DATADOG_SERVICE_NAME') or 'celery'


def meta_from_context(context):
    """ helper to extract meta values from a celery context """
    meta_keys = (
        'correlation_id', 'delivery_info', 'eta', 'expires', 'hostname',
        'id', 'reply_to', 'retries', 'timelimit',
    )

    meta = dict()
    for name in meta_keys:
        value = context.get(name)

        # Skip this key if it is not set
        if value is None:
            continue

        # Skip `timelimit` if it is not set (it's default/unset value is `(None, None)`)
        if name == 'timelimit' and value == (None, None):
            continue

        # Skip `retries` if it's value is `0`
        if name == 'retries' and value == 0:
            continue

        # prefix the tag as 'celery'
        tag_name = 'celery.{}'.format(name)
        meta[tag_name] = value
    return meta


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
