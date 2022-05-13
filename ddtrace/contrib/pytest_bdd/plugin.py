import json
import os

import pytest

from ddtrace.contrib.pytest.plugin import _extract_span as _extract_feature_span
from ddtrace.contrib.pytest_bdd.constants import STEP_KIND
from ddtrace.ext import test
from ddtrace.pin import Pin


def _extract_span(item):
    """Extract span from `step_func`."""
    return getattr(item, "_datadog_span", None)


def _store_span(item, span):
    """Store span at `step_func`."""
    setattr(item, "_datadog_span", span)


def pytest_sessionstart(session):
    if session.config.pluginmanager.hasplugin("pytest-bdd"):
        session.config.pluginmanager.register(_PytestBddPlugin, "_datadog-pytest-bdd")


class _PytestBddPlugin:
    @staticmethod
    @pytest.hookimpl(tryfirst=True)
    def pytest_bdd_before_scenario(request, feature, scenario):
        pin = Pin.get_from(request.config)
        if pin:
            span = _extract_feature_span(request.node)
            if span is not None:
                location = os.path.relpath(scenario.feature.filename, str(request.config.rootdir))
                span.set_tag(test.NAME, scenario.name)
                span.set_tag(test.SUITE, location)  # override test suite name with .feature location
                # TODO: add code owner of the location after https://github.com/DataDog/dd-trace-py/pull/3635 is merged

    @staticmethod
    @pytest.hookimpl(tryfirst=True)
    def pytest_bdd_before_step(request, feature, scenario, step, step_func):
        pin = Pin.get_from(request.config)
        if pin:
            feature_span = _extract_feature_span(request.node)
            span = pin.tracer.start_span(
                STEP_KIND,
                resource=step.name,
                span_type=step.type,
                child_of=feature_span,
                activate=True,
            )

            # store parsed step arguments
            parser = getattr(step_func, "parser", None)
            if parser is not None:
                converters = getattr(step_func, "converters", {})
                parameters = {}
                try:
                    for arg, value in parser.parse_arguments(step.name).items():
                        try:
                            if arg in converters:
                                value = converters[arg](value)
                        except Exception:
                            # Ignore invalid converters
                            pass
                        parameters[arg] = value
                except Exception:
                    pass
                if parameters:
                    span.set_tag(test.PARAMETERS, json.dumps(parameters))

            location = os.path.relpath(step_func.__code__.co_filename, str(request.config.rootdir))
            span.set_tag(test.FILE, location)
            # TODO: extract code owners for the step "location"
            _store_span(step_func, span)

    @staticmethod
    @pytest.hookimpl(trylast=True)
    def pytest_bdd_after_step(request, feature, scenario, step, step_func, step_func_args):
        span = _extract_span(step_func)
        if span is not None:
            span.finish()

    @staticmethod
    def pytest_bdd_step_error(request, feature, scenario, step, step_func, step_func_args, exception):
        span = _extract_span(step_func)
        if span is not None:
            span.set_exc_info(type(exception), exception, exception.__traceback__)
            span.finish()
