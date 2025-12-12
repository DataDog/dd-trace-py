from __future__ import annotations

import json
import logging
from pathlib import Path
import sys
import typing as t

import pytest

import ddtrace
from ddtrace.testing.internal.pytest.utils import item_to_test_ref
from ddtrace.testing.internal.test_data import TestTag


if t.TYPE_CHECKING:
    from pytest import FixtureRequest
    from pytest_bdd.parser import Feature
    from pytest_bdd.parser import Scenario
    from pytest_bdd.parser import Step

    from ddtrace.testing.internal.pytest.plugin import TestOptPlugin

FRAMEWORK = "pytest_bdd"
STEP_KIND = "pytest_bdd.step"


log = logging.getLogger(__name__)


class BddTestOptPlugin:
    def __init__(self, main_plugin: TestOptPlugin) -> None:
        self.main_plugin = main_plugin
        self.framework_version = self._get_framework_version()

    def _get_framework_version(self) -> str:
        import importlib.metadata as importlib_metadata

        return str(importlib_metadata.version("pytest-bdd"))

    def _get_workspace_relative_path(self, feature_path_str: str) -> Path:
        feature_path = Path(feature_path_str).resolve()
        workspace_path = self.main_plugin.manager.workspace_path
        try:
            return feature_path.relative_to(workspace_path)
        except ValueError:  # noqa: E722
            log.debug("Feature path %s is not relative to workspace path %s", feature_path, workspace_path)
        return feature_path

    @pytest.hookimpl(tryfirst=True)
    def pytest_bdd_before_scenario(self, request: FixtureRequest, feature: Feature, scenario: Scenario) -> None:
        test_ref = item_to_test_ref(request.node)
        test = self.main_plugin.manager.get_test(test_ref)
        if not test:
            log.debug("Could not find test %s", test_ref)
            return

        feature_path = self._get_workspace_relative_path(scenario.feature.filename)
        codeowners = self._get_codeowners(feature_path)

        test.tags[TestTag.TEST_NAME] = scenario.name
        test.tags[TestTag.TEST_SUITE] = str(feature_path)
        if codeowners:
            test.set_codeowners(codeowners)

    @pytest.hookimpl(tryfirst=True)
    def pytest_bdd_before_step(
        self, request: FixtureRequest, feature: Feature, scenario: Scenario, step: Step, step_func: t.Callable
    ) -> None:
        tracer = ddtrace.tracer
        if tracer is None:
            return

        feature_span = tracer.current_root_span()

        span = tracer.start_span(
            step.type,
            resource=step.name,
            span_type=STEP_KIND,
            child_of=feature_span,
            activate=True,
        )
        span.set_tag(TestTag.COMPONENT, FRAMEWORK)
        span.set_tag(TestTag.TEST_FRAMEWORK, FRAMEWORK)
        span.set_tag(TestTag.TEST_FRAMEWORK_VERSION, self.framework_version)

        feature_path = self._get_workspace_relative_path(scenario.feature.filename)
        codeowners = self._get_codeowners(feature_path)

        span.set_tag(TestTag.TEST_FILE, str(feature_path))
        if codeowners:
            span.set_tag(TestTag.CODEOWNERS, json.dumps(codeowners))

        setattr(step_func, "_datadog_span", span)

    @pytest.hookimpl(trylast=True)
    def pytest_bdd_after_step(
        self,
        request: FixtureRequest,
        feature: Feature,
        scenario: Scenario,
        step: Step,
        step_func: t.Callable,
        step_func_args: t.Any,
    ) -> None:
        span = getattr(step_func, "_datadog_span", None)
        if span is not None:
            step_func_args_json = _get_step_func_args_json(step, step_func, step_func_args)
            if step_func_args:
                span.set_tag(TestTag.PARAMETERS, step_func_args_json)
            span.finish()

    @pytest.hookimpl(trylast=True)
    def pytest_bdd_step_error(
        self,
        request: FixtureRequest,
        feature: Feature,
        scenario: Scenario,
        step: Step,
        step_func: t.Callable,
        step_func_args: t.Any,
        exception: Exception,
    ) -> None:
        span = getattr(step_func, "_datadog_span", None)
        if span is not None:
            if hasattr(exception, "__traceback__"):
                tb = exception.__traceback__
            else:
                # PY2 compatibility workaround
                _, _, tb = sys.exc_info()
            if step_func_args:
                step_func_args_json = _get_step_func_args_json(step, step_func, step_func_args)
                span.set_tag(TestTag.PARAMETERS, step_func_args_json)
            span.set_exc_info(type(exception), exception, tb)
            span.finish()

    def _get_codeowners(self, feature_path: Path) -> t.Optional[t.List[str]]:
        if codeowners := self.main_plugin.manager.codeowners:
            return codeowners.of(str(feature_path))
        return None


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
