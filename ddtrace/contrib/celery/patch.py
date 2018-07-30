import celery

from celery import signals

from .app import patch_app, unpatch_app

from .signals import (
    trace_prerun,
    trace_postrun,
    trace_before_publish,
    trace_after_publish,
    trace_failure,
)


def patch():
    """Instrument Celery base application and the `TaskRegistry` so
    that any new registered task is automatically instrumented. In the
    case of Django-Celery integration, also the `@shared_task` decorator
    must be instrumented because Django doesn't use the Celery registry.
    """
    patch_app(celery.Celery)
    signals.task_prerun.connect(trace_prerun)
    signals.task_postrun.connect(trace_postrun)
    signals.before_task_publish.connect(trace_before_publish)
    signals.after_task_publish.connect(trace_after_publish)
    signals.task_failure.connect(trace_failure)


def unpatch():
    """Disconnect all signals and remove Tracing capabilities"""
    unpatch_app(celery.Celery)
    signals.task_prerun.disconnect(trace_prerun)
    signals.task_postrun.disconnect(trace_postrun)
    signals.before_task_publish.disconnect(trace_before_publish)
    signals.after_task_publish.disconnect(trace_after_publish)
    signals.task_failure.disconnect(trace_failure)
