import os

from ddtrace.contrib.pytest.plugin import _extract_span as _extract_feature_span
from ddtrace.ext import test
from ddtrace.pin import Pin


def _extract_span(item):
    """Extract span from `step_func`."""
    return getattr(item, "_datadog_span", None)


def _store_span(item, span):
    """Store span at `step_func`."""
    setattr(item, "_datadog_span", span)


def pytest_bdd_before_scenario(request, feature, scenario):
    pin = Pin.get_from(request.config)
    if pin:
        span = _extract_feature_span(request.node)
        if span is not None:
            span.set_tag(test.NAME, scenario.name)
            span.set_tag(test.SUITE, scenario.feature.filename)
            # TODO: add code owner of the "scenario.feature.filename"


def pytest_bdd_before_step(request, feature, scenario, step, step_func):
    pin = Pin.get_from(request.config)
    if pin:
        feature_span = _extract_feature_span(request.node)
        span = pin.tracer.start_span(
            step.type,
            resource=step.name,
            span_type=step.type,
            child_of=feature_span,
            activate=True,
        )
        location = os.path.relpath(step_func.__code__.co_filename, str(request.config.rootdir))
        span.set_tag(test.FILE, location)
        # TODO: extract code owners for the step "location"
        _store_span(step_func, span)


def pytest_bdd_after_step(request, feature, scenario, step, step_func, step_func_args):
    span = _extract_span(step_func)
    if span is not None:
        span.finish()


def pytest_bdd_step_error(request, feature, scenario, step, step_func, step_func_args, exception):
    span = _extract_span(step_func)
    if span is not None:
        span.set_exc_info(type(exception), exception, exception.__traceback__)
        span.finish()
