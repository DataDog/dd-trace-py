"""Tests Disabling functionality"""
from unittest import mock

import pytest

from ddtrace.internal.ci_visibility._api_client import TestManagementSettings
from ddtrace.internal.ci_visibility._api_client import TestProperties
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from tests.ci_visibility.api_client._util import _make_fqdn_internal_test_id
from tests.contrib.pytest.test_pytest import PytestTestCaseBase
from tests.contrib.pytest.test_pytest import _get_spans_from_list
from tests.contrib.pytest.utils import assert_stats


_TEST_PROPERTIES = {
    _make_fqdn_internal_test_id("", "test_disabled.py", "test_disabled"): TestProperties(disabled=True),
    _make_fqdn_internal_test_id("", "test_quarantined.py", "test_quarantined"): TestProperties(quarantined=True),
    _make_fqdn_internal_test_id(
        "", "test_disabled_and_quarantined.py", "test_disabled_and_quarantined"
    ): TestProperties(
        quarantined=True,
        disabled=True,
    ),
}

_TEST_DISABLED = """
def test_disabled():
    assert False
"""

_TEST_QUARANTINED = """
def test_quarantined():
    assert False
"""

_TEST_DISABLED_AND_QUARANTINED = """
def test_disabled_and_quarantined():
    assert False
"""

_TEST_PASS = """
def test_pass():
    assert True
"""


class PytestDisablingTestCase(PytestTestCaseBase):
    @pytest.fixture(autouse=True, scope="function")
    def set_up_test_management(self):
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(
                test_management=TestManagementSettings(enabled=True),
                flaky_test_retries_enabled=False,
            ),
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_test_management_tests",
            return_value=_TEST_PROPERTIES,
        ):
            yield

    def test_disabled_no_ddtrace_does_not_disable(self):
        self.testdir.makepyfile(test_disabled=_TEST_DISABLED)
        self.testdir.makepyfile(test_pass=_TEST_PASS)
        rec = self.inline_run("-q")
        assert rec.ret == 1
        assert_stats(rec, passed=1, failed=1)
        assert len(self.pop_spans()) == 0

    def test_disabled_test(self):
        self.testdir.makepyfile(test_disabled=_TEST_DISABLED)
        self.testdir.makepyfile(test_pass=_TEST_PASS)
        rec = self.inline_run("--ddtrace", "-q")
        assert rec.ret == 0
        assert_stats(rec, passed=1, skipped=1)

        spans = self.pop_spans()
        [session_span] = _get_spans_from_list(spans, "session")
        [module_span] = _get_spans_from_list(spans, "module")
        [suite_span_disabled] = _get_spans_from_list(spans, "suite", "test_disabled.py")
        [suite_span_pass] = _get_spans_from_list(spans, "suite", "test_pass.py")

        [test_span_disabled] = _get_spans_from_list(spans, "test", "test_disabled")
        assert test_span_disabled.get_tag("test.test_management.is_quarantined") is None
        assert test_span_disabled.get_tag("test.test_management.is_test_disabled") == "true"
        assert test_span_disabled.get_tag("test.status") == "skip"

        [test_span_pass] = _get_spans_from_list(spans, "test", "test_pass")
        assert test_span_pass.get_tag("test.test_management.is_quarantined") is None
        assert test_span_pass.get_tag("test.test_management.is_test_disabled") is None
        assert test_span_pass.get_tag("test.status") == "pass"

    def test_disabled_and_quarantined_test(self):
        self.testdir.makepyfile(test_disabled_and_quarantined=_TEST_DISABLED_AND_QUARANTINED)
        rec = self.inline_run("--ddtrace", "-q")
        assert rec.ret == 0
        assert_stats(rec, passed=0, skipped=1)

        spans = self.pop_spans()
        [session_span] = _get_spans_from_list(spans, "session")
        [module_span] = _get_spans_from_list(spans, "module")
        [suite_span] = _get_spans_from_list(spans, "suite", "test_disabled_and_quarantined.py")

        [test_span] = _get_spans_from_list(spans, "test", "test_disabled_and_quarantined")
        assert test_span.get_tag("test.test_management.is_quarantined") == "true"
        assert test_span.get_tag("test.test_management.is_test_disabled") == "true"
        assert test_span.get_tag("test.status") == "skip"
