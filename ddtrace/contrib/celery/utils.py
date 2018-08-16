from weakref import WeakValueDictionary

from .constants import CTX_KEY


def tags_from_context(context):
    """Helper to extract meta values from a Celery Context"""
    tag_keys = (
        'compression', 'correlation_id', 'countdown', 'delivery_info', 'eta',
        'exchange', 'expires', 'hostname', 'id', 'priority', 'queue', 'reply_to',
        'retries', 'routing_key', 'serializer', 'timelimit', 'origin', 'state',
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

        # Celery 4.0 uses `origin` instead of `hostname`; this change preserves
        # the same name for the tag despite Celery version
        if key == 'origin':
            key = 'hostname'

        # prefix the tag as 'celery'
        tag_name = 'celery.{}'.format(key)
        tags[tag_name] = value
    return tags


def attach_span(task, task_id, span):
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


def detach_span(task, task_id):
    """Helper to remove a `Span` in a Celery task when it's propagated.
    This function handles tasks where the `Span` is not attached.
    """
    weak_dict = getattr(task, CTX_KEY, None)
    if weak_dict is None:
        return

    weak_dict.pop(task_id, None)


def retrieve_span(task, task_id):
    """Helper to retrieve an active `Span` stored in a `Task`
    instance
    """
    weak_dict = getattr(task, CTX_KEY, None)
    if weak_dict is None:
        return
    else:
        return weak_dict.get(task_id)


def retrieve_task_id(context):
    """Helper to retrieve the `Task` identifier from the message `body`.
    This helper supports Protocol Version 1 and 2. The Protocol is well
    detailed in the official documentation:
    http://docs.celeryproject.org/en/latest/internals/protocol.html
    """
    headers = context.get('headers')
    body = context.get('body')
    if headers:
        # Protocol Version 2 (default from Celery 4.0)
        return headers.get('id')
    else:
        # Protocol Version 1
        return body.get('id')
