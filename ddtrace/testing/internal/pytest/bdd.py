from __future__ import annotations

from pathlib import Path
import sys

import pytest

from ddtrace.contrib.internal.pytest._utils import _get_test_id_from_item
from ddtrace.contrib.internal.pytest_bdd._plugin import _extract_span
from ddtrace.contrib.internal.pytest_bdd._plugin import _get_step_func_args_json
from ddtrace.contrib.internal.pytest_bdd._plugin import _store_span
from ddtrace.contrib.internal.pytest_bdd.constants import FRAMEWORK
from ddtrace.contrib.internal.pytest_bdd.constants import STEP_KIND
from ddtrace.contrib.internal.pytest_bdd.patch import get_version
from ddtrace.ext import test
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility.api import InternalTest
from ddtrace.internal.test_visibility.api import InternalTestSession


import logging

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
    def pytest_bdd_before_scenario(self, request, feature, scenario):
        from .plugin import nodeid_to_test_ref
        test_ref = nodeid_to_test_ref(request.node.nodeid)
        test = self.main_plugin.manager.get_test(test_ref)
        feature_path = self._get_workspace_relative_path(scenario.feature.filename)
        codeowners = self.main_plugin.manager.codeowners.of(str(feature_path)) if self.main_plugin.manager.codeowners else None

        #print(">>>> override scenario attributes", dict(test_ref=test_ref, name=scenario.name, suite_name=str(feature_path), codeowners=codeowners))
        test.tags["test.name"] = scenario.name
        test.tags["test.suite"] = str(feature_path)
        test.set_codeowners(codeowners)



    @pytest.hookimpl(tryfirst=True)
    def pytest_bdd_before_step(self, request, feature, scenario, step, step_func):
        import ddtrace
        tracer = ddtrace.tracer
        if tracer is None:
            return

        #test_ref = nodeid_to_test_ref(request.node)
        feature_span = tracer.current_root_span()

        span = tracer.start_span(
            step.type,
            resource=step.name,
            span_type=STEP_KIND,
            child_of=feature_span,
            activate=True,
        )
        span._set_tag_str("component", "pytest_bdd")

        span.set_tag(test.FRAMEWORK, FRAMEWORK)
        span.set_tag(test.FRAMEWORK_VERSION, self.framework_version)

        feature_path = self._get_workspace_relative_path(scenario.feature.filename)

        span.set_tag(test.FILE, str(feature_path))
        span.set_tag(test.CODEOWNERS, InternalTestSession.get_path_codeowners(feature_path))
        step_func._datadog_span = span


    @pytest.hookimpl(trylast=True)
    def pytest_bdd_after_step(self, request, feature, scenario, step, step_func, step_func_args):
        span = getattr(step_func, "_datadog_span", None)
        if span is not None:
            step_func_args_json = _get_step_func_args_json(step, step_func, step_func_args)
            if step_func_args:
                span.set_tag(test.PARAMETERS, step_func_args_json)
            span.finish()

    @pytest.hookimpl(trylast=True)
    def pytest_bdd_step_error(self, request, feature, scenario, step, step_func, step_func_args, exception):
        span = getattr(step_func, "_datadog_span", None)
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
