from ddtrace import Pin

from celery import registry

from . import constants as c
from .util import tags_from_context, propagate_span, retrieve_span


def trace_prerun(*args, **kwargs):
    # safe-guard to avoid crashes in case the signals API
    # changes in Celery
    task = kwargs.get('sender')
    task_id = kwargs.get('task_id')
    if task is None or task_id is None:
        return

    # retrieve the task Pin or fallback to the global one
    pin = Pin.get_from(task) or Pin.get_from(task.app)
    if pin is None:
        return

    # propagate the `Span` in the current task Context
    span = pin.tracer.trace(c.WORKER_ROOT_SPAN, service=c.WORKER_SERVICE, resource=task.name)
    propagate_span(task, task_id, span)


def trace_postrun(*args, **kwargs):
    # safe-guard to avoid crashes in case the signals API
    # changes in Celery
    task = kwargs.get('sender')
    task_id = kwargs.get('task_id')
    if task is None or task_id is None:
        return

    # retrieve and finish the Span
    span = retrieve_span(task, task_id)
    if span is None:
        return
    else:
        # request context tags
        span.set_tag(c.TASK_TAG_KEY, c.TASK_RUN)
        span.set_tags(tags_from_context(task.request))
        # response tags
        span.set_tag('celery.state', kwargs.get('state'))
        span.finish()


def trace_before_publish(*args, **kwargs):
    # safe-guard to avoid crashes in case the signals API
    # changes in Celery
    task_name = None
    headers = kwargs.get('headers')
    if headers is not None:
        task_name = headers.get('task')
        if task_name is None:
            return

    # `before_task_publish` signal doesn't propagate the task instance so
    # we need to retrieve it from the Celery Registry to access the `Pin`. The
    # `Task` instance **does not** include any information about the current
    # execution, so it **must not** be used to retrieve `request` data.
    task = registry.tasks.get(task_name)
    task_id = headers.get('id')
    if task is None or task_id is None:
        return

    # propagate the `Span` in the current task Context
    pin = Pin.get_from(task) or Pin.get_from(task.app)
    if pin is None:
        return
    span = pin.tracer.trace(c.PRODUCER_ROOT_SPAN, service=c.PRODUCER_SERVICE, resource=task_name)
    propagate_span(task, task_id, span)


def trace_after_publish(*args, **kwargs):
    # safe-guard to avoid crashes in case the signals API
    # changes in Celery
    task_name = None
    headers = kwargs.get('headers')
    if headers is not None:
        task_name = headers.get('task')
        if task_name is None:
            return

    task = registry.tasks.get(task_name)
    task_id = headers.get('id')
    if task is None or task_id is None:
        return

    # retrieve and finish the Span
    span = retrieve_span(task, task_id)
    if span is None:
        return
    else:
        # tags from headers context
        # Note: adding tags from `traceback` or `state` calls will make an
        # API call to the backend for the properties so we should rely
        # only on the given `Context`
        span.set_tag(c.TASK_TAG_KEY, c.TASK_APPLY_ASYNC)
        span.set_tags(tags_from_context(kwargs))
        span.set_tags(tags_from_context(headers))
        span.finish()


def trace_failure(*args, **kwargs):
    # safe-guard to avoid crashes in case the signals API
    # changes in Celery
    task = kwargs.get('sender')
    task_id = kwargs.get('task_id')
    if task is None or task_id is None:
        return

    # retrieve and finish the Span
    span = retrieve_span(task, task_id)
    if span is None:
        return
    else:
        # add Exception tags; post signals are still called
        # so we don't need to attach other tags here
        ex = kwargs.get('einfo')
        if ex is None:
            return
        span.set_exc_info(ex.type, ex.exception, ex.tb)
