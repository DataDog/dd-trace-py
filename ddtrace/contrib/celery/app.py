import celery
from celery import signals

from ddtrace import Pin
from ddtrace import config
from ddtrace.pin import _DD_PIN_NAME

from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_KIND
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanKind
from ...ext import SpanTypes
from .. import trace_utils
from .signals import trace_after_publish
from .signals import trace_before_publish
from .signals import trace_failure
from .signals import trace_postrun
from .signals import trace_prerun
from .signals import trace_retry


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

        with pin.tracer.trace(
            "celery.beat.{}".format(fn_name),
            span_type=SpanTypes.WORKER,
            service=trace_utils.ext_service(pin, integration_config),
        ) as span:
            if resource_fn:
                span.resource = resource_fn(args)
            span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)
            rate = config.celery.get_analytics_sample_rate()
            if rate is not None:
                span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, rate)
            span.set_tag(SPAN_MEASURED_KEY)

            return func(*args, **kwargs)

    return _traced_beat_inner
