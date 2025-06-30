from unittest import mock

import pytest

from ddtrace.contrib.internal.pytest._utils import _pytest_version_supports_itr
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from ddtrace.internal.ci_visibility.constants import COVERAGE_TAG_NAME
from tests.contrib.pytest.test_pytest import PytestTestCaseBase
from tests.contrib.pytest.test_pytest import _get_spans_from_list


_TEST_PASS_CONTENT = """
def test_func_pass():
    assert True
"""

pytestmark = pytest.mark.skipif(not _pytest_version_supports_itr(), reason="pytest version does not support coverage")


class PytestEarlyConfigTestCase(PytestTestCaseBase):
    """
    Test that code coverage is enabled in the `pytest_load_initial_conftests` hook, regardless of the method used to
    enable ddtrace (command line, env var, or ini file).
    """

    @pytest.fixture(autouse=True, scope="function")
    def set_up_features(self):
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(coverage_enabled=True),
        ):
            yield

    def test_coverage_not_enabled(self):
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.inline_run()
        spans = self.pop_spans()
        assert not spans

    def test_coverage_enabled_via_command_line_option(self):
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.inline_run("--ddtrace")
        spans = self.pop_spans()
        [suite_span] = _get_spans_from_list(spans, "suite")
        [test_span] = _get_spans_from_list(spans, "test")
        assert (
            suite_span.get_struct_tag(COVERAGE_TAG_NAME) is not None or test_span.get_tag(COVERAGE_TAG_NAME) is not None
        )

    def test_coverage_enabled_via_pytest_addopts_env_var(self):
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.inline_run(extra_env={"PYTEST_ADDOPTS": "--ddtrace"})
        spans = self.pop_spans()
        [suite_span] = _get_spans_from_list(spans, "suite")
        [test_span] = _get_spans_from_list(spans, "test")
        assert (
            suite_span.get_struct_tag(COVERAGE_TAG_NAME) is not None or test_span.get_tag(COVERAGE_TAG_NAME) is not None
        )

    def test_coverage_enabled_via_addopts_ini_file_option(self):
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.testdir.makefile(".ini", pytest="[pytest]\naddopts = --ddtrace\n")
        self.inline_run()
        spans = self.pop_spans()
        [suite_span] = _get_spans_from_list(spans, "suite")
        [test_span] = _get_spans_from_list(spans, "test")
        assert (
            suite_span.get_struct_tag(COVERAGE_TAG_NAME) is not None or test_span.get_tag(COVERAGE_TAG_NAME) is not None
        )

    def test_coverage_enabled_via_ddtrace_ini_file_option(self):
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.testdir.makefile(".ini", pytest="[pytest]\nddtrace = 1\n")
        self.inline_run()
        spans = self.pop_spans()
        [suite_span] = _get_spans_from_list(spans, "suite")
        [test_span] = _get_spans_from_list(spans, "test")
        assert (
            suite_span.get_struct_tag(COVERAGE_TAG_NAME) is not None or test_span.get_tag(COVERAGE_TAG_NAME) is not None
        )
