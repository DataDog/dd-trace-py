"""Tests Attempt-to-Fix functionality, and its interaction with Quarantine, Disabling, and Test Impact Analysis."""

from unittest import mock
from xml.etree import ElementTree

import pytest

from ddtrace.contrib.internal.pytest._utils import _USE_PLUGIN_V2
from ddtrace.contrib.internal.pytest._utils import _pytest_version_supports_attempt_to_fix
from ddtrace.internal.ci_visibility._api_client import ITRData
from ddtrace.internal.ci_visibility._api_client import TestManagementSettings
from ddtrace.internal.ci_visibility._api_client import TestProperties
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from tests.ci_visibility.api_client._util import _make_fqdn_internal_test_id
from tests.ci_visibility.api_client._util import _make_fqdn_suite_id
from tests.contrib.pytest.test_pytest import PytestTestCaseBase
from tests.contrib.pytest.test_pytest import _get_spans_from_list
from tests.contrib.pytest.utils import assert_stats


pytestmark = pytest.mark.skipif(
    not (_USE_PLUGIN_V2 and _pytest_version_supports_attempt_to_fix()),
    reason="Attempt-to-Fix requires v2 of the plugin and pytest >=7.0",
)

_TEST_PASS = """
def test_pass():
    assert True
"""

_TEST_FAIL = """
def test_fail():
    assert False
"""

_TEST_SKIP = """
import pytest

def test_skip():
    pytest.skip()
"""


_TEST_FLAKY = """
count = 0

def test_flaky():
    global count
    count += 1
    assert count == 11
"""

_TEST_PROPERTIES = {
    _make_fqdn_internal_test_id("", "test_quarantined.py", "test_pass"): TestProperties(
        quarantined=True,
        attempt_to_fix=True,
    ),
    _make_fqdn_internal_test_id("", "test_quarantined.py", "test_fail"): TestProperties(
        quarantined=True,
        attempt_to_fix=True,
    ),
    _make_fqdn_internal_test_id("", "test_quarantined.py", "test_flaky"): TestProperties(
        quarantined=True,
        attempt_to_fix=True,
    ),
    _make_fqdn_internal_test_id("", "test_disabled.py", "test_pass"): TestProperties(
        disabled=True,
        attempt_to_fix=True,
    ),
    _make_fqdn_internal_test_id("", "test_skippable.py", "test_fail"): TestProperties(
        disabled=True,
        attempt_to_fix=True,
    ),
    _make_fqdn_internal_test_id("", "test_active.py", "test_pass"): TestProperties(
        attempt_to_fix=True,
    ),
    _make_fqdn_internal_test_id("", "test_active.py", "test_fail"): TestProperties(
        attempt_to_fix=True,
    ),
    _make_fqdn_internal_test_id("", "test_active.py", "test_skip"): TestProperties(
        attempt_to_fix=True,
    ),
}


class PytestAttemptToFixTestCase(PytestTestCaseBase):
    @pytest.fixture(autouse=True, scope="function")
    def set_up_test_management(self):
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(
                test_management=TestManagementSettings(
                    enabled=True,
                    attempt_to_fix_retries=10,
                ),
                flaky_test_retries_enabled=False,
            ),
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_test_management_tests",
            return_value=_TEST_PROPERTIES,
        ):
            yield

    def test_attempt_to_fix_quarantined_test_pass(self):
        self.testdir.makepyfile(test_quarantined=_TEST_PASS)
        rec = self.inline_run("--ddtrace", "-q")
        assert rec.ret == 0
        assert_stats(rec, quarantined=1, passed=0, failed=0)

        spans = self.pop_spans()
        [session_span] = _get_spans_from_list(spans, "session")
        [module_span] = _get_spans_from_list(spans, "module")
        [suite_span] = _get_spans_from_list(spans, "suite", "test_quarantined.py")
        test_spans = _get_spans_from_list(spans, "test")

        assert len(test_spans) == 11
        for span in test_spans:
            assert span.get_tag("test.test_management.is_quarantined") == "true"
            assert span.get_tag("test.status") == "pass"

        assert test_spans[-1].get_tag("test.test_management.attempt_to_fix_passed") == "true"
        assert test_spans[-1].get_tag("test.has_failed_all_retries") is None

    def test_attempt_to_fix_quarantined_test_fail(self):
        self.testdir.makepyfile(test_quarantined=_TEST_FAIL)
        rec = self.inline_run("--ddtrace", "-q")
        assert rec.ret == 0
        assert_stats(rec, quarantined=1, passed=0, failed=0)

        spans = self.pop_spans()
        [session_span] = _get_spans_from_list(spans, "session")
        [module_span] = _get_spans_from_list(spans, "module")
        [suite_span] = _get_spans_from_list(spans, "suite", "test_quarantined.py")
        test_spans = _get_spans_from_list(spans, "test")

        assert len(test_spans) == 11
        for span in test_spans:
            assert span.get_tag("test.test_management.is_quarantined") == "true"
            assert span.get_tag("test.status") == "fail"

        assert test_spans[-1].get_tag("test.test_management.attempt_to_fix_passed") == "false"
        assert test_spans[-1].get_tag("test.has_failed_all_retries") == "true"

    def test_attempt_to_fix_quarantined_test_flaky(self):
        self.testdir.makepyfile(test_quarantined=_TEST_FLAKY)
        rec = self.inline_run("--ddtrace", "-q")
        assert rec.ret == 0
        assert_stats(rec, quarantined=1, passed=0, failed=0)

        spans = self.pop_spans()
        [session_span] = _get_spans_from_list(spans, "session")
        [module_span] = _get_spans_from_list(spans, "module")
        [suite_span] = _get_spans_from_list(spans, "suite", "test_quarantined.py")
        test_spans = _get_spans_from_list(spans, "test")

        assert len(test_spans) == 11
        for i, span in enumerate(test_spans):
            assert span.get_tag("test.test_management.is_quarantined") == "true"
            assert span.get_tag("test.status") == ("pass" if i == 10 else "fail")

        assert test_spans[-1].get_tag("test.test_management.attempt_to_fix_passed") == "false"
        assert test_spans[-1].get_tag("test.has_failed_all_retries") is None

    def test_attempt_to_fix_disabled_test(self):
        self.testdir.makepyfile(test_disabled=_TEST_PASS)
        rec = self.inline_run("--ddtrace", "-q")
        assert rec.ret == 0
        assert_stats(rec, quarantined=1, passed=0, failed=0)

        spans = self.pop_spans()
        [session_span] = _get_spans_from_list(spans, "session")
        [module_span] = _get_spans_from_list(spans, "module")
        [suite_span] = _get_spans_from_list(spans, "suite", "test_disabled.py")
        test_spans = _get_spans_from_list(spans, "test")

        assert len(test_spans) == 11
        for span in test_spans:
            assert span.get_tag("test.test_management.is_quarantined") is None
            assert span.get_tag("test.test_management.is_test_disabled") == "true"
            assert span.get_tag("test.status") == "pass"

        assert test_spans[-1].get_tag("test.test_management.attempt_to_fix_passed") == "true"
        assert test_spans[-1].get_tag("test.has_failed_all_retries") is None

    def test_attempt_to_fix_active_test_pass(self):
        self.testdir.makepyfile(test_active=_TEST_PASS)
        rec = self.inline_run("--ddtrace", "-q")
        assert rec.ret == 0
        assert_stats(rec, quarantined=0, passed=1, failed=0)

        spans = self.pop_spans()
        [session_span] = _get_spans_from_list(spans, "session")
        [module_span] = _get_spans_from_list(spans, "module")
        [suite_span] = _get_spans_from_list(spans, "suite", "test_active.py")
        test_spans = _get_spans_from_list(spans, "test")

        assert len(test_spans) == 11
        for i, span in enumerate(test_spans):
            assert span.get_tag("test.test_management.is_quarantined") is None
            assert span.get_tag("test.test_management.is_test_disabled") is None
            assert span.get_tag("test.status") == "pass"

        assert test_spans[-1].get_tag("test.test_management.attempt_to_fix_passed") == "true"
        assert test_spans[-1].get_tag("test.has_failed_all_retries") is None

    def test_attempt_to_fix_active_test_fail(self):
        self.testdir.makepyfile(test_active=_TEST_FAIL)
        rec = self.inline_run("--ddtrace", "-v", "-s")
        assert rec.ret == 1
        assert_stats(rec, quarantined=0, passed=0, failed=1)

        spans = self.pop_spans()
        [session_span] = _get_spans_from_list(spans, "session")
        [module_span] = _get_spans_from_list(spans, "module")
        [suite_span] = _get_spans_from_list(spans, "suite", "test_active.py")
        test_spans = _get_spans_from_list(spans, "test")

        assert len(test_spans) == 11
        for i, span in enumerate(test_spans):
            assert span.get_tag("test.test_management.is_quarantined") is None
            assert span.get_tag("test.test_management.is_test_disabled") is None
            assert span.get_tag("test.status") == "fail"

        assert test_spans[-1].get_tag("test.test_management.attempt_to_fix_passed") == "false"
        assert test_spans[-1].get_tag("test.has_failed_all_retries") == "true"

    def test_attempt_to_fix_active_test_skip(self):
        self.testdir.makepyfile(test_active=_TEST_SKIP)
        rec = self.inline_run("--ddtrace", "-v", "-s")
        assert rec.ret == 0
        assert_stats(rec, quarantined=0, passed=0, failed=0, skipped=1)

        spans = self.pop_spans()
        [session_span] = _get_spans_from_list(spans, "session")
        [module_span] = _get_spans_from_list(spans, "module")
        [suite_span] = _get_spans_from_list(spans, "suite", "test_active.py")
        test_spans = _get_spans_from_list(spans, "test")

        assert len(test_spans) == 11
        for i, span in enumerate(test_spans):
            assert span.get_tag("test.test_management.is_quarantined") is None
            assert span.get_tag("test.test_management.is_test_disabled") is None
            assert span.get_tag("test.status") == "skip"

        assert test_spans[-1].get_tag("test.test_management.attempt_to_fix_passed") == "false"
        assert test_spans[-1].get_tag("test.has_failed_all_retries") is None

    def test_pytest_attempt_to_fix_junit_xml_active(self):
        self.testdir.makepyfile(test_active=_TEST_PASS + _TEST_FAIL + _TEST_SKIP)

        rec = self.inline_run("--ddtrace", "--junit-xml=out.xml", "-v", "-s")
        assert rec.ret == 1

        test_suite = ElementTree.parse(f"{self.testdir}/out.xml").find("testsuite")
        assert test_suite.attrib["tests"] == "3"
        assert test_suite.attrib["failures"] == "1"
        assert test_suite.attrib["skipped"] == "1"
        assert test_suite.attrib["errors"] == "0"

    @pytest.mark.xfail(reason="JUnit XML miscounts quarantined tests")
    def test_pytest_attempt_to_fix_junit_xml_quarantined(self):
        self.testdir.makepyfile(test_quarantined=_TEST_PASS + _TEST_FAIL + _TEST_SKIP)

        rec = self.inline_run("--ddtrace", "--junit-xml=out.xml", "-v", "-s")
        assert rec.ret == 0

        test_suite = ElementTree.parse(f"{self.testdir}/out.xml").find("testsuite")
        assert test_suite.attrib["tests"] == "3"  # we currently get "2"
        assert test_suite.attrib["failures"] == "0"
        assert test_suite.attrib["skipped"] == "1"
        assert test_suite.attrib["errors"] == "0"


class PytestAttemptToFixITRTestCase(PytestTestCaseBase):
    @pytest.fixture(autouse=True, scope="function")
    def set_up_test_management(self):
        def _mock_fetch_tests_to_skip(self, *_, **__):
            self._itr_data = ITRData(skippable_items={_make_fqdn_suite_id("", "test_skippable.py")})

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(
                test_management=TestManagementSettings(
                    enabled=True,
                    attempt_to_fix_retries=10,
                ),
                skipping_enabled=True,
                flaky_test_retries_enabled=False,
            ),
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_test_management_tests",
            return_value=_TEST_PROPERTIES,
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip",
            _mock_fetch_tests_to_skip,
        ):
            yield

    def test_attempt_to_fix_itr_skipped_test(self):
        self.testdir.makepyfile(test_skippable=_TEST_PASS + _TEST_FAIL)
        rec = self.inline_run("--ddtrace", "-q")
        assert rec.ret == 0
        assert_stats(rec, quarantined=1, passed=0, failed=0, skipped=1)

        spans = self.pop_spans()
        [session_span] = _get_spans_from_list(spans, "session")
        [module_span] = _get_spans_from_list(spans, "module")
        [suite_span] = _get_spans_from_list(spans, "suite", "test_skippable.py")
        [skipped_span] = _get_spans_from_list(spans, "test", "test_pass")
        attempt_to_fix_spans = _get_spans_from_list(spans, "test", "test_fail")

        assert skipped_span.get_tag("test.test_management.is_quarantined") is None
        assert skipped_span.get_tag("test.test_management.is_test_disabled") is None
        assert skipped_span.get_tag("test.status") == "skip"

        assert len(attempt_to_fix_spans) == 11
        for span in attempt_to_fix_spans:
            assert span.get_tag("test.test_management.is_quarantined") is None
            assert span.get_tag("test.test_management.is_test_disabled") == "true"
            assert span.get_tag("test.status") == "fail"

        assert attempt_to_fix_spans[-1].get_tag("test.test_management.attempt_to_fix_passed") == "false"
        assert attempt_to_fix_spans[-1].get_tag("test.has_failed_all_retries") == "true"
