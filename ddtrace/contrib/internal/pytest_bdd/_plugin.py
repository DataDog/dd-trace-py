import json
import os
import sys

import pytest

from ddtrace.contrib.internal.pytest._utils import _extract_span as _extract_feature_span
from ddtrace.contrib.internal.pytest_bdd.constants import FRAMEWORK
from ddtrace.contrib.internal.pytest_bdd.constants import STEP_KIND
from ddtrace.contrib.internal.pytest_bdd.patch import get_version
from ddtrace.ext import test
from ddtrace.internal.ci_visibility import CIVisibility as _CIVisibility
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def _extract_span(item):
    """Extract span from `step_func`."""
    return getattr(item, "_datadog_span", None)


def _store_span(item, span):
    """Store span at `step_func`."""
    item._datadog_span = span


def _extract_step_func_args(step, step_func, step_func_args):
    """Backwards-compatible get arguments from step_func or step_func_args"""
    if not (hasattr(step_func, "parser") or hasattr(step_func, "_pytest_bdd_parsers")):
        return step_func_args

    # store parsed step arguments
    try:
        parsers = [step_func.parser]
    except AttributeError:
        try:
            # pytest-bdd >= 6.0.0
            parsers = step_func._pytest_bdd_parsers
        except AttributeError:
            parsers = []
    for parser in parsers:
        if parser is not None:
            converters = getattr(step_func, "converters", {})
            parameters = {}
            try:
                for arg, value in parser.parse_arguments(step.name).items():
                    try:
                        if arg in converters:
                            value = converters[arg](value)
                    except Exception:
                        log.debug("argument conversion failed.")
                    parameters[arg] = value
            except Exception:
                log.debug("argument parsing failed.")

    return parameters or None


def _get_step_func_args_json(step, step_func, step_func_args):
    """Get step function args as JSON, catching serialization errors"""
    try:
        extracted_step_func_args = _extract_step_func_args(step, step_func, step_func_args)
        if extracted_step_func_args:
            return json.dumps(extracted_step_func_args)
        return None
    except TypeError as err:
        log.debug("Could not serialize arguments", exc_info=True)
        return json.dumps({"error_serializing_args": str(err)})


class _PytestBddPlugin:
    def __init__(self):
        self.framework_version = get_version()

    @staticmethod
    @pytest.hookimpl(tryfirst=True)
    def pytest_bdd_before_scenario(request, feature, scenario):
        if _CIVisibility.enabled:
            span = _extract_feature_span(request.node)
            if span is not None:
                location = os.path.relpath(scenario.feature.filename, str(request.config.rootdir))
                span.set_tag(test.NAME, scenario.name)
                span.set_tag(test.SUITE, location)  # override test suite name with .feature location

                _CIVisibility.set_codeowners_of(location, span=span)

    @pytest.hookimpl(tryfirst=True)
    def pytest_bdd_before_step(self, request, feature, scenario, step, step_func):
        if _CIVisibility.enabled:
            feature_span = _extract_feature_span(request.node)
            span = _CIVisibility._instance.tracer.start_span(
                step.type,
                resource=step.name,
                span_type=STEP_KIND,
                child_of=feature_span,
                activate=True,
            )
            span.set_tag_str("component", "pytest_bdd")

            span.set_tag(test.FRAMEWORK, FRAMEWORK)
            span.set_tag(test.FRAMEWORK_VERSION, self.framework_version)

            location = os.path.relpath(step_func.__code__.co_filename, str(request.config.rootdir))
            span.set_tag(test.FILE, location)
            _CIVisibility.set_codeowners_of(location, span=span)

            _store_span(step_func, span)

    @staticmethod
    @pytest.hookimpl(trylast=True)
    def pytest_bdd_after_step(request, feature, scenario, step, step_func, step_func_args):
        span = _extract_span(step_func)
        if span is not None:
            step_func_args_json = _get_step_func_args_json(step, step_func, step_func_args)
            if step_func_args:
                span.set_tag(test.PARAMETERS, step_func_args_json)
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
            step_func_args_json = _get_step_func_args_json(step, step_func, step_func_args)
            if step_func_args:
                span.set_tag(test.PARAMETERS, step_func_args_json)
            span.set_exc_info(type(exception), exception, tb)
            span.finish()
