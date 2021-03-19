from celery import registry

from ddtrace import Pin
from ddtrace import config

from . import constants as c
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ...internal.logger import get_logger
from ...propagation.http import HTTPPropagator
from .utils import attach_span
from .utils import detach_span
from .utils import retrieve_span
from .utils import retrieve_task_id
from .utils import tags_from_context


log = get_logger(__name__)
propagator = HTTPPropagator


def trace_prerun(*args, **kwargs):
    # safe-guard to avoid crashes in case the signals API
    # changes in Celery
    task = kwargs.get("sender")
    task_id = kwargs.get("task_id")
    log.debug("prerun signal start task_id=%s", task_id)
    if task is None or task_id is None:
        log.debug("unable to extract the Task and the task_id. This version of Celery may not be supported.")
        return

    # retrieve the task Pin or fallback to the global one
    pin = Pin.get_from(task) or Pin.get_from(task.app)
    if pin is None:
        log.debug("no pin found on task or task.app task_id=%s", task_id)
        return

    if config.celery["distributed_tracing"]:
        context = propagator.extract(task.request.get("headers", {}))
        if context.trace_id:
            pin.tracer.context_provider.activate(context)

    # propagate the `Span` in the current task Context
    service = config.celery["worker_service_name"]
    span = pin.tracer.trace(c.WORKER_ROOT_SPAN, service=service, resource=task.name, span_type=SpanTypes.WORKER)
    # set analytics sample rate
    rate = config.celery.get_analytics_sample_rate()
    if rate is not None:
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, rate)

    span.set_tag(SPAN_MEASURED_KEY)
    attach_span(task, task_id, span)


def trace_postrun(*args, **kwargs):
    # safe-guard to avoid crashes in case the signals API
    # changes in Celery
    task = kwargs.get("sender")
    task_id = kwargs.get("task_id")
    log.debug("postrun signal task_id=%s", task_id)
    if task is None or task_id is None:
        log.debug("unable to extract the Task and the task_id. This version of Celery may not be supported.")
        return

    # retrieve and finish the Span
    span = retrieve_span(task, task_id)
    if span is None:
        log.warning("no existing span found for task_id=%s", task_id)
        return
    else:
        # request context tags
        span.set_tag(c.TASK_TAG_KEY, c.TASK_RUN)
        span.set_tags(tags_from_context(kwargs))
        span.set_tags(tags_from_context(task.request))
        span.finish()
        detach_span(task, task_id)


def trace_before_publish(*args, **kwargs):
    # `before_task_publish` signal doesn't propagate the task instance so
    # we need to retrieve it from the Celery Registry to access the `Pin`. The
    # `Task` instance **does not** include any information about the current
    # execution, so it **must not** be used to retrieve `request` data.
    task_name = kwargs.get("sender")
    task = registry.tasks.get(task_name)
    task_id = retrieve_task_id(kwargs)
    # safe-guard to avoid crashes in case the signals API
    # changes in Celery
    if task is None or task_id is None:
        log.debug("unable to extract the Task and the task_id. This version of Celery may not be supported.")
        return

    # propagate the `Span` in the current task Context
    pin = Pin.get_from(task) or Pin.get_from(task.app)
    if pin is None:
        return

    # apply some tags here because most of the data is not available
    # in the task_after_publish signal
    service = config.celery["producer_service_name"]
    span = pin.tracer.trace(c.PRODUCER_ROOT_SPAN, service=service, resource=task_name)
    # set analytics sample rate
    rate = config.celery.get_analytics_sample_rate()
    if rate is not None:
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, rate)

    span.set_tag(SPAN_MEASURED_KEY)
    span.set_tag(c.TASK_TAG_KEY, c.TASK_APPLY_ASYNC)
    span.set_tag("celery.id", task_id)
    span.set_tags(tags_from_context(kwargs))

    # Note: adding tags from `traceback` or `state` calls will make an
    # API call to the backend for the properties so we should rely
    # only on the given `Context`
    attach_span(task, task_id, span, is_publish=True)

    if config.celery["distributed_tracing"]:
        trace_headers = {}
        propagator.inject(span.context, trace_headers)

        # This weirdness is due to yet another Celery bug concerning
        # how headers get propagated in async flows
        # https://github.com/celery/celery/issues/4875
        task_headers = kwargs.get("headers") or {}
        task_headers.setdefault("headers", {})
        task_headers["headers"].update(trace_headers)
        kwargs["headers"] = task_headers


def trace_after_publish(*args, **kwargs):
    task_name = kwargs.get("sender")
    task = registry.tasks.get(task_name)
    task_id = retrieve_task_id(kwargs)
    # safe-guard to avoid crashes in case the signals API
    # changes in Celery
    if task is None or task_id is None:
        log.debug("unable to extract the Task and the task_id. This version of Celery may not be supported.")
        return

    # retrieve and finish the Span
    span = retrieve_span(task, task_id, is_publish=True)
    if span is None:
        return
    else:
        span.finish()
        detach_span(task, task_id, is_publish=True)


def trace_failure(*args, **kwargs):
    # safe-guard to avoid crashes in case the signals API
    # changes in Celery
    task = kwargs.get("sender")
    task_id = kwargs.get("task_id")
    if task is None or task_id is None:
        log.debug("unable to extract the Task and the task_id. This version of Celery may not be supported.")
        return

    # retrieve and finish the Span
    span = retrieve_span(task, task_id)
    if span is None:
        return
    else:
        # add Exception tags; post signals are still called
        # so we don't need to attach other tags here
        ex = kwargs.get("einfo")
        if ex is None:
            return
        if hasattr(task, "throws") and isinstance(ex.exception, task.throws):
            return
        span.set_exc_info(ex.type, ex.exception, ex.tb)


def trace_retry(*args, **kwargs):
    # safe-guard to avoid crashes in case the signals API
    # changes in Celery
    task = kwargs.get("sender")
    context = kwargs.get("request")
    if task is None or context is None:
        log.debug("unable to extract the Task or the Context. This version of Celery may not be supported.")
        return

    reason = kwargs.get("reason")
    if not reason:
        log.debug("unable to extract the retry reason. This version of Celery may not be supported.")
        return

    span = retrieve_span(task, context.id)
    if span is None:
        return

    # Add retry reason metadata to span
    # DEV: Use `str(reason)` instead of `reason.message` in case we get something that isn't an `Exception`
    span.set_tag(c.TASK_RETRY_REASON_KEY, str(reason))
