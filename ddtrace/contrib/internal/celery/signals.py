from celery import registry
from celery.utils import nodenames

from ddtrace import Pin
from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.celery import constants as c
from ddtrace.contrib.internal.celery.utils import attach_span
from ddtrace.contrib.internal.celery.utils import detach_span
from ddtrace.contrib.internal.celery.utils import retrieve_span
from ddtrace.contrib.internal.celery.utils import retrieve_task_id
from ddtrace.contrib.internal.celery.utils import set_tags_from_context
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import net
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.propagation.http import HTTPPropagator


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

    request_headers = task.request.get("headers", {})
    trace_utils.activate_distributed_headers(pin.tracer, int_config=config.celery, request_headers=request_headers)

    # propagate the `Span` in the current task Context
    service = config.celery["worker_service_name"]
    span = pin.tracer.trace(c.WORKER_ROOT_SPAN, service=service, resource=task.name, span_type=SpanTypes.WORKER)

    # Store an item called "prerun span" in case task_postrun doesn't get called
    core.set_item("prerun_span", span)

    # set span.kind to the type of request being performed
    span.set_tag_str(SPAN_KIND, SpanKind.CONSUMER)

    # set component tag equal to name of integration
    span.set_tag_str(COMPONENT, config.celery.integration_name)

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
        span.set_tag_str(c.TASK_TAG_KEY, c.TASK_RUN)
        set_tags_from_context(span, kwargs)
        set_tags_from_context(span, task.request.__dict__)
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

    # Store an item called "task span" in case after_task_publish doesn't get called
    core.set_item("task_span", span)

    span.set_tag_str(COMPONENT, config.celery.integration_name)

    # set span.kind to the type of request being performed
    span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)

    # set analytics sample rate
    rate = config.celery.get_analytics_sample_rate()
    if rate is not None:
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, rate)

    span.set_tag(SPAN_MEASURED_KEY)
    span.set_tag_str(c.TASK_TAG_KEY, c.TASK_APPLY_ASYNC)
    span.set_tag_str("celery.id", task_id)
    set_tags_from_context(span, kwargs)
    if kwargs.get("headers") is not None:
        # required to extract hostname from origin header on `celery>=4.0`
        set_tags_from_context(span, kwargs["headers"])

    # Note: adding tags from `traceback` or `state` calls will make an
    # API call to the backend for the properties so we should rely
    # only on the given `Context`
    attach_span(task, task_id, span, is_publish=True)

    if config.celery["distributed_tracing"]:
        trace_headers = {}
        propagator.inject(span.context, trace_headers)

        # put distributed trace headers where celery will propagate them
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
        nodename = span.get_tag("celery.hostname")
        if nodename is not None:
            _, host = nodenames.nodesplit(nodename)
            span.set_tag_str(net.TARGET_HOST, host)

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

        if hasattr(task, "throws"):
            original_exception = ex.exception
            if hasattr(original_exception, "exc"):
                # Python 3.11+ support: The original exception is wrapped in an `exc` attribute
                original_exception = original_exception.exc

            if isinstance(original_exception, task.throws):
                return

        # ensure we are getting the actual exception class which stores the exception message
        exc = ex.exception
        if hasattr(exc, "exc"):
            exc = exc.exc

        span.set_exc_info(ex.type, exc, ex.tb)


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
    span.set_tag_str(c.TASK_RETRY_REASON_KEY, str(reason))
