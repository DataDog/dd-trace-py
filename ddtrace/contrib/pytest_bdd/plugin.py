import json
import os
import sys

import pytest

from ddtrace.contrib.pytest.plugin import _extract_span as _extract_feature_span
from ddtrace.contrib.pytest_bdd.constants import FRAMEWORK
from ddtrace.contrib.pytest_bdd.constants import STEP_KIND
from ddtrace.ext import test
from ddtrace.internal.ci.recorder import CIRecorder
from ddtrace.internal.logger import get_logger



log = get_logger(__name__)


def _extract_span(item):
    """Extract span from `step_func`."""
    return getattr(item, "_datadog_span", None)


def _store_span(item, span):
    """Store span at `step_func`."""
    setattr(item, "_datadog_span", span)


def pytest_sessionstart(session):
    if session.config.pluginmanager.hasplugin("pytest-bdd"):
        session.config.pluginmanager.register(_PytestBddPlugin(), "_datadog-pytest-bdd")


class _PytestBddPlugin:
    def __init__(self):
        import pytest_bdd

        self.framework_version = pytest_bdd.__version__

    @staticmethod
    @pytest.hookimpl(tryfirst=True)
    def pytest_bdd_before_scenario(request, feature, scenario):
        if CIRecorder.enabled:
            span = _extract_feature_span(request.node)
            if span is not None:
                location = os.path.relpath(scenario.feature.filename, str(request.config.rootdir))
                span.set_tag(test.NAME, scenario.name)
                span.set_tag(test.SUITE, location)  # override test suite name with .feature location

                CIRecorder.set_codeowners_of(location, span=span)

    @pytest.hookimpl(tryfirst=True)
    def pytest_bdd_before_step(self, request, feature, scenario, step, step_func):
        if CIRecorder.enabled:
            feature_span = _extract_feature_span(request.node)
            span = CIRecorder._instance.tracer.start_span(
                step.type,
                resource=step.name,
                span_type=STEP_KIND,
                child_of=feature_span,
                activate=True,
            )
            span.set_tag(test.FRAMEWORK, FRAMEWORK)
            span.set_tag(test.FRAMEWORK_VERSION, self.framework_version)

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
            CIRecorder.set_codeowners_of(location, span=span)

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
            if hasattr(exception, "__traceback__"):
                tb = exception.__traceback__
            else:
                # PY2 compatibility workaround
                _, _, tb = sys.exc_info()
            span.set_exc_info(type(exception), exception, tb)
            span.finish()
