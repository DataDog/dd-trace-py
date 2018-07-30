# stdlib
import os

from weakref import WeakValueDictionary

# Project
from ddtrace import Pin

from .constants import CTX_KEY

# Service info
APP = 'celery'
PRODUCER_SERVICE = os.environ.get('DATADOG_SERVICE_NAME') or 'celery-producer'
WORKER_SERVICE = os.environ.get('DATADOG_SERVICE_NAME') or 'celery-worker'


def tags_from_context(context):
    """Helper to extract meta values from a Celery Context"""
    tag_keys = (
        'compression', 'correlation_id', 'countdown', 'delivery_info', 'eta',
        'exchange', 'expires', 'hostname', 'id', 'priority', 'queue', 'reply_to',
        'retries', 'routing_key', 'serializer', 'timelimit',
    )

    tags = {}
    for key in tag_keys:
        value = context.get(key)

        # Skip this key if it is not set
        if value is None or value == '':
            continue

        # Skip `timelimit` if it is not set (it's default/unset value is a
        # tuple or a list of `None` values
        if key == 'timelimit' and value in [(None, None), [None, None]]:
            continue

        # Skip `retries` if it's value is `0`
        if key == 'retries' and value == 0:
            continue

        # prefix the tag as 'celery'
        tag_name = 'celery.{}'.format(key)
        tags[tag_name] = value
    return tags


def propagate_span(task, task_id, span):
    """Helper to propagate a `Span` for the given `Task` instance. This
    function uses a `WeakValueDictionary` that stores a Datadog Span using
    the `task_id` as a key. This is useful when information must be
    propagated from one Celery signal to another.
    """
    weak_dict = getattr(task, CTX_KEY, None)
    if weak_dict is None:
        weak_dict = WeakValueDictionary()
        setattr(task, CTX_KEY, weak_dict)

    weak_dict[task_id] = span


def retrieve_span(task, task_id):
    """Helper to retrieve an active `Span` stored in a `Task`
    instance
    """
    weak_dict = getattr(task, CTX_KEY, None)
    if weak_dict is None:
        return
    else:
        return weak_dict.get(task_id)


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
