import sys

import celery
from celery import signals

from ddtrace import config
from ddtrace._trace.pin import _DD_PIN_NAME
from ddtrace._trace.pin import Pin
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.celery.signals import trace_after_publish
from ddtrace.contrib.internal.celery.signals import trace_before_publish
from ddtrace.contrib.internal.celery.signals import trace_failure
from ddtrace.contrib.internal.celery.signals import trace_postrun
from ddtrace.contrib.internal.celery.signals import trace_prerun
from ddtrace.contrib.internal.celery.signals import trace_retry
from ddtrace.contrib.internal.trace_utils import set_service_and_source
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.forksafe import ddtrace_after_in_parent
from ddtrace.internal.forksafe import ddtrace_before_fork
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native_runtime import get_native_runtime
from ddtrace.trace import tracer


log = get_logger(__name__)


def patch_app(app, pin=None):
    """Attach the Pin class to the application and connect
    our handlers to Celery signals.
    """
    if getattr(app, "__datadog_patch", False):
        return
    app.__datadog_patch = True

    # attach the PIN object
    pin = pin or Pin(
        service=config.celery["worker_service_name"],
        _config=config.celery,
    )
    pin.onto(app)

    trace_utils.wrap(
        "celery.beat",
        "Scheduler.apply_entry",
        _traced_beat_function(config.celery, "apply_entry", lambda args: args[0].name),
    )
    trace_utils.wrap("celery.beat", "Scheduler.tick", _traced_beat_function(config.celery, "tick"))
    pin.onto(celery.beat.Scheduler)

    # Patch apply_async
    trace_utils.wrap("celery.app.task", "Task.apply_async", _traced_apply_async_function(config.celery, "apply_async"))

    # When celery starts the embedded beat process it closes every inherited file descriptor with
    # `close_open_fds`. We pause the native runtime around it to prevent it from using a stale fd.
    trace_utils.wrap("celery.platforms", "close_open_fds", _patched_close_open_fds)

    # connect to the Signal framework
    signals.task_prerun.connect(trace_prerun, weak=False)
    signals.task_postrun.connect(trace_postrun, weak=False)
    signals.before_task_publish.connect(trace_before_publish, weak=False)
    signals.after_task_publish.connect(trace_after_publish, weak=False)
    signals.task_failure.connect(trace_failure, weak=False)
    signals.task_retry.connect(trace_retry, weak=False)
    return app


def unpatch_app(app):
    """Remove the Pin instance from the application and disconnect
    our handlers from Celery signal framework.
    """
    if not getattr(app, "__datadog_patch", False):
        return
    app.__datadog_patch = False

    pin = Pin.get_from(app)
    if pin is not None:
        delattr(app, _DD_PIN_NAME)

    trace_utils.unwrap(celery.beat.Scheduler, "apply_entry")
    trace_utils.unwrap(celery.beat.Scheduler, "tick")
    trace_utils.unwrap(celery.app.task.Task, "apply_async")
    trace_utils.unwrap(celery.platforms, "close_open_fds")

    signals.task_prerun.disconnect(trace_prerun)
    signals.task_postrun.disconnect(trace_postrun)
    signals.before_task_publish.disconnect(trace_before_publish)
    signals.after_task_publish.disconnect(trace_after_publish)
    signals.task_failure.disconnect(trace_failure)
    signals.task_retry.disconnect(trace_retry)


def _traced_beat_function(integration_config, fn_name, resource_fn=None):
    def _traced_beat_inner(func, instance, args, kwargs):
        pin = Pin.get_from(instance)
        if not pin or not pin.enabled():
            return func(*args, **kwargs)

        with tracer.trace(
            f"celery.beat.{fn_name}",
            span_type=SpanTypes.WORKER,
        ) as span:
            set_service_and_source(span, trace_utils.ext_service(pin, integration_config), integration_config)
            if resource_fn:
                span.resource = resource_fn(args)
            span._set_attribute(SPAN_KIND, SpanKind.PRODUCER)
            span._set_attribute(_SPAN_MEASURED_KEY, 1)

            return func(*args, **kwargs)

    return _traced_beat_inner


def _traced_apply_async_function(integration_config, fn_name, resource_fn=None):
    """
    When apply_async is called, it calls various Celery signals in order, which gets used
    to start and close the span.
    Example: before_task_publish starts the span while after_task_publish closes the span.
    If an exception occurs anywhere inside Celery or its dependencies, this can interrupt the
    closing signals.
    The purpose of _traced_apply_async_function is to close the spans even if one of the closing
    signals don't get called over the course of the apply_task lifecycle.
    This is done by fetching the stored span and closing it if it hasn't already been closed by a
    closing signal.
    """

    def _traced_apply_async_inner(func, instance, args, kwargs):
        with core.context_with_data("task_context"):
            try:
                return func(*args, **kwargs)
            except Exception:
                # If an internal exception occurs, record the exception in the span,
                # then raise the Celery error as usual
                task_span = core.find_item("task_span")
                if task_span:
                    task_span.set_exc_info(*sys.exc_info())

                raise
            finally:
                task_span = core.find_item("task_span")
                if task_span:
                    log.debug(
                        "The after_task_publish signal was not called, so manually closing span: %r",
                        task_span,
                    )
                    task_span.finish()

    return _traced_apply_async_inner


def _patched_close_open_fds(func, instance, args, kwargs):
    """Pause ddtrace while Celery closes inherited file descriptors.

    The before-fork hooks stop periodic threads for the whole cleanup, so no callback can start or
    rebuild the native runtime while descriptors are being closed. The native runtime itself is paused
    right before the sweep and resumed right after, so its descriptors are recreated once the sweep is
    done. A runtime inherited through a fork and not rebuilt yet is left alone; it rebuilds lazily on
    its next use, after the sweep. Python-level sockets (for example database connections) are still
    closed by the sweep and must reconnect.
    """
    log.debug("Stopping ddtrace threads before closing fds")
    ddtrace_before_fork()
    try:
        runtime = _started_native_runtime()
        if runtime is None or runtime.restart_pending():
            return func(*args, **kwargs)

        runtime.before_fork()
        try:
            return func(*args, **kwargs)
        finally:
            # No native call may run between the sweep and this resume.
            runtime.after_fork_parent()
    finally:
        ddtrace_after_in_parent()
        log.debug("Restarted ddtrace threads after closing fds")


def _started_native_runtime():
    # Do not create the runtime just to pause it.
    if get_native_runtime.cache_info().currsize == 0:
        return None
    return get_native_runtime()
