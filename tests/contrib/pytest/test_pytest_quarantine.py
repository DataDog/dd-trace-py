"""Tests Quarantine functionality

The tests in this module only validate the behavior of Quarantine, so only counts and statuses of tests, retries, and
sessions are checked.

- The same known tests are used to override fetching of known tests.
- The session object is patched to never be a faulty session, by default.
"""
from unittest import mock

import pytest

from ddtrace.contrib.internal.pytest._utils import _pytest_version_supports_efd
from ddtrace.internal.ci_visibility._api_client import TestManagementSettings
from ddtrace.internal.ci_visibility._api_client import TestProperties
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from tests.ci_visibility.api_client._util import _make_fqdn_internal_test_id
from tests.contrib.pytest.test_pytest import PytestTestCaseBase
from tests.contrib.pytest.test_pytest import _get_spans_from_list
from tests.contrib.pytest.utils import assert_stats


pytestmark = pytest.mark.skipif(not _pytest_version_supports_efd(), reason="Quarantine requires pytest >=7.0")

_TEST_PASS_QUARANTINED = """
import pytest

def test_pass_quarantined():
    assert True
"""

_TEST_PASS_UNQUARANTINED = """
import pytest

def test_pass_normal():
    assert True
"""

_TEST_FAIL_QUARANTINED = """
import pytest

def test_fail_quarantined():
    assert False
"""

_TEST_FAIL_UNQUARANTINED = """
import pytest

def test_fail_normal():
    assert False
"""

_TEST_FAIL_SETUP_QUARANTINED = """
import pytest

@pytest.fixture()
def fail_setup():
    raise ValueError("fail setup")

def test_fail_setup(fail_setup):
    assert True
"""

_TEST_FAIL_TEARDOWN_QUARANTINED = """
import pytest

@pytest.fixture()
def fail_teardown():
    yield
    raise ValueError("fail teardown")

def test_fail_teardown(fail_teardown):
    assert True
"""

_TEST_PROPERTIES = {
    _make_fqdn_internal_test_id("", "test_pass_quarantined.py", "test_pass_quarantined"): TestProperties(
        quarantined=True
    ),
    _make_fqdn_internal_test_id("", "test_fail_quarantined.py", "test_fail_quarantined"): TestProperties(
        quarantined=True
    ),
    _make_fqdn_internal_test_id("", "test_fail_setup_quarantined.py", "test_fail_setup"): TestProperties(
        quarantined=True
    ),
    _make_fqdn_internal_test_id("", "test_fail_teardown_quarantined.py", "test_fail_teardown"): TestProperties(
        quarantined=True
    ),
}


class PytestQuarantineTestCase(PytestTestCaseBase):
    @pytest.fixture(autouse=True, scope="function")
    def set_up_quarantine(self):
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

    def test_fail_quarantined_no_ddtrace_does_not_quarantine(self):
        self.testdir.makepyfile(test_pass_quarantined=_TEST_PASS_QUARANTINED)
        self.testdir.makepyfile(test_pass_normal=_TEST_PASS_UNQUARANTINED)
        self.testdir.makepyfile(test_fail_quarantined=_TEST_FAIL_QUARANTINED)
        self.testdir.makepyfile(test_fail_normal=_TEST_FAIL_UNQUARANTINED)
        rec = self.inline_run("-q")
        assert rec.ret == 1
        assert_stats(rec, passed=2, failed=2)
        assert len(self.pop_spans()) == 0  # ddtrace disabled, not collecting traces

    def test_fail_quarantined_with_ddtrace_does_not_fail_session(self):
        self.testdir.makepyfile(test_pass_quarantined=_TEST_PASS_QUARANTINED)
        self.testdir.makepyfile(test_fail_quarantined=_TEST_FAIL_QUARANTINED)

        rec = self.inline_run("--ddtrace", "-q")

        assert rec.ret == 0
        assert_stats(rec, quarantined=2)

        assert len(self.pop_spans()) > 0

    def test_failing_and_passing_quarantined_and_unquarantined_tests(self):
        self.testdir.makepyfile(test_pass_quarantined=_TEST_PASS_QUARANTINED)
        self.testdir.makepyfile(test_pass_normal=_TEST_PASS_UNQUARANTINED)
        self.testdir.makepyfile(test_fail_quarantined=_TEST_FAIL_QUARANTINED)
        self.testdir.makepyfile(test_fail_normal=_TEST_FAIL_UNQUARANTINED)

        rec = self.inline_run("--ddtrace", "-q")
        assert rec.ret == 1
        assert_stats(rec, quarantined=2, passed=1, failed=1)

        assert len(self.pop_spans()) > 0

    def test_env_var_disables_quarantine(self):
        self.testdir.makepyfile(test_fail_quarantined=_TEST_FAIL_QUARANTINED)

        rec = self.inline_run("--ddtrace", "-q", extra_env={"DD_TEST_MANAGEMENT_ENABLED": "0"})

        assert rec.ret == 1
        assert_stats(rec, quarantined=0, failed=1)

        assert len(self.pop_spans()) > 0

    def test_env_var_does_not_override_api(self):
        """Environment variable works as a kill-switch; if quarantine is disabled in the API,
        the env var cannot make it enabled.
        """
        self.testdir.makepyfile(test_fail_quarantined=_TEST_FAIL_QUARANTINED)

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(
                test_management=TestManagementSettings(enabled=False),
            ),
        ):
            rec = self.inline_run("--ddtrace", "-q", extra_env={"DD_TEST_MANAGEMENT_ENABLED": "1"})

        assert rec.ret == 1
        assert_stats(rec, failed=1)

        assert len(self.pop_spans()) > 0

    def test_quarantine_outcomes_without_atr(self):
        self.testdir.makepyfile(test_fail_quarantined=_TEST_FAIL_QUARANTINED)

        rec = self.inline_run("--ddtrace", "-q")
        assert rec.ret == 0
        assert_stats(rec, quarantined=1)

        outcomes = [(call.report.when, call.report.outcome) for call in rec.getcalls("pytest_report_teststatus")]
        assert outcomes == [
            ("setup", "passed"),
            ("call", "quarantined"),
            ("teardown", "passed"),
        ]

        assert len(rec.getcalls("pytest_pyfunc_call")) == 1

    def test_quarantine_outcomes_with_atr(self):
        self.testdir.makepyfile(test_fail_quarantined=_TEST_FAIL_QUARANTINED)

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(
                test_management=TestManagementSettings(enabled=True),
                flaky_test_retries_enabled=True,
            ),
        ):
            rec = self.inline_run("--ddtrace", "-q")

        assert rec.ret == 0
        assert_stats(rec, quarantined=1)

        outcomes = [(call.report.when, call.report.outcome) for call in rec.getcalls("pytest_report_teststatus")]
        assert outcomes == [
            ("setup", "passed"),
            ("call", "dd_quarantine_atr_attempt_failed"),
            ("call", "dd_quarantine_atr_attempt_failed"),
            ("call", "dd_quarantine_atr_attempt_failed"),
            ("call", "dd_quarantine_atr_attempt_failed"),
            ("call", "dd_quarantine_atr_attempt_failed"),
            ("call", "dd_quarantine_atr_attempt_failed"),
            ("call", "dd_quarantine_atr_final_failed"),
            ("teardown", "passed"),
        ]

        assert len(rec.getcalls("pytest_pyfunc_call")) == 6

    def test_quarantine_fail_setup(self):
        self.testdir.makepyfile(test_fail_setup_quarantined=_TEST_FAIL_SETUP_QUARANTINED)

        rec = self.inline_run("--ddtrace", "-q")

        assert rec.ret == 0
        assert_stats(rec, quarantined=1)

        assert len(self.pop_spans()) > 0

    def test_quarantine_fail_teardown(self):
        self.testdir.makepyfile(test_fail_teardown_quarantined=_TEST_FAIL_TEARDOWN_QUARANTINED)

        rec = self.inline_run("--ddtrace", "-q")

        assert rec.ret == 0
        assert_stats(rec, quarantined=1)

        assert len(self.pop_spans()) > 0

    def test_quarantine_spans_without_atr(self):
        self.testdir.makepyfile(test_pass_quarantined=_TEST_PASS_QUARANTINED)
        self.testdir.makepyfile(test_fail_quarantined=_TEST_FAIL_QUARANTINED)

        rec = self.inline_run("--ddtrace", "-q")

        assert rec.ret == 0
        assert_stats(rec, quarantined=2)

        spans = self.pop_spans()

        [session_span] = _get_spans_from_list(spans, "session")
        assert session_span.get_tag("test.test_management.enabled") == "true"

        [module_span] = _get_spans_from_list(spans, "module")
        [suite_span_fail_quarantined] = _get_spans_from_list(spans, "suite", "test_fail_quarantined.py")
        [suite_span_pass_quarantined] = _get_spans_from_list(spans, "suite", "test_pass_quarantined.py")

        [test_span_fail_quarantined] = _get_spans_from_list(spans, "test", "test_fail_quarantined")
        assert test_span_fail_quarantined.get_tag("test.test_management.is_quarantined") == "true"
        assert test_span_fail_quarantined.get_tag("test.status") == "fail"

        [test_span_pass_quarantined] = _get_spans_from_list(spans, "test", "test_pass_quarantined")
        assert test_span_pass_quarantined.get_tag("test.test_management.is_quarantined") == "true"
        assert test_span_pass_quarantined.get_tag("test.status") == "pass"

    def test_quarantine_spans_with_atr(self):
        self.testdir.makepyfile(test_pass_quarantined=_TEST_PASS_QUARANTINED)
        self.testdir.makepyfile(test_fail_quarantined=_TEST_FAIL_QUARANTINED)

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(
                test_management=TestManagementSettings(enabled=True),
                flaky_test_retries_enabled=True,
            ),
        ):
            rec = self.inline_run("--ddtrace", "-q")

        assert rec.ret == 0
        assert_stats(rec, quarantined=2)

        spans = self.pop_spans()

        [session_span] = _get_spans_from_list(spans, "session")
        assert session_span.get_tag("test.test_management.enabled") == "true"

        [module_span] = _get_spans_from_list(spans, "module")
        [suite_span_fail_quarantined] = _get_spans_from_list(spans, "suite", "test_fail_quarantined.py")
        [suite_span_pass_quarantined] = _get_spans_from_list(spans, "suite", "test_pass_quarantined.py")

        test_spans_fail_quarantined = _get_spans_from_list(spans, "test", "test_fail_quarantined")
        assert len(test_spans_fail_quarantined) == 6
        assert all(
            span.get_tag("test.test_management.is_quarantined") == "true" for span in test_spans_fail_quarantined
        )
        assert all(span.get_tag("test.status") == "fail" for span in test_spans_fail_quarantined)
        assert test_spans_fail_quarantined[0].get_tag("test.is_retry") is None
        assert all(span.get_tag("test.is_retry") for span in test_spans_fail_quarantined[1:])

        [test_span_pass_quarantined] = _get_spans_from_list(spans, "test", "test_pass_quarantined")
        assert test_span_pass_quarantined.get_tag("test.test_management.is_quarantined") == "true"
        assert test_span_pass_quarantined.get_tag("test.status") == "pass"


class PytestQuarantineSkippingTestCase(PytestTestCaseBase):
    @pytest.fixture(autouse=True, scope="function")
    def set_up_quarantine(self):
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

    def test_fail_quarantined_no_ddtrace_does_not_quarantine(self):
        self.testdir.makepyfile(test_pass_quarantined=_TEST_PASS_QUARANTINED)
        self.testdir.makepyfile(test_pass_normal=_TEST_PASS_UNQUARANTINED)
        self.testdir.makepyfile(test_fail_quarantined=_TEST_FAIL_QUARANTINED)
        self.testdir.makepyfile(test_fail_normal=_TEST_FAIL_UNQUARANTINED)
        rec = self.inline_run("-q", extra_env={"_DD_TEST_SKIP_QUARANTINED_TESTS": "true"})
        assert rec.ret == 1
        assert_stats(rec, passed=2, failed=2)
        assert len(self.pop_spans()) == 0  # ddtrace disabled, not collecting traces

    def test_fail_quarantined_with_ddtrace_does_not_fail_session(self):
        self.testdir.makepyfile(test_pass_quarantined=_TEST_PASS_QUARANTINED)
        self.testdir.makepyfile(test_fail_quarantined=_TEST_FAIL_QUARANTINED)

        rec = self.inline_run("--ddtrace", "-q", extra_env={"_DD_TEST_SKIP_QUARANTINED_TESTS": "true"})

        assert rec.ret == 0
        assert_stats(rec, skipped=2)

        assert len(self.pop_spans()) > 0

    def test_failing_and_passing_quarantined_and_unquarantined_tests(self):
        self.testdir.makepyfile(test_pass_quarantined=_TEST_PASS_QUARANTINED)
        self.testdir.makepyfile(test_pass_normal=_TEST_PASS_UNQUARANTINED)
        self.testdir.makepyfile(test_fail_quarantined=_TEST_FAIL_QUARANTINED)
        self.testdir.makepyfile(test_fail_normal=_TEST_FAIL_UNQUARANTINED)

        rec = self.inline_run("--ddtrace", "-q", extra_env={"_DD_TEST_SKIP_QUARANTINED_TESTS": "true"})
        assert rec.ret == 1
        assert_stats(rec, skipped=2, passed=1, failed=1)

        assert len(self.pop_spans()) > 0

    def test_quarantine_outcomes_without_atr(self):
        return self._test_quarantine_outcomes(atr_enabled=False)

    def test_quarantine_outcomes_with_atr(self):
        return self._test_quarantine_outcomes(atr_enabled=True)

    def _test_quarantine_outcomes(self, atr_enabled):
        # ATR should not retry tests skipped by quarantine.
        self.testdir.makepyfile(test_fail_quarantined=_TEST_FAIL_QUARANTINED)

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(
                test_management=TestManagementSettings(enabled=True),
                flaky_test_retries_enabled=atr_enabled,
            ),
        ):
            rec = self.inline_run("--ddtrace", "-q", extra_env={"_DD_TEST_SKIP_QUARANTINED_TESTS": "true"})

        assert rec.ret == 0
        assert_stats(rec, skipped=1)

        outcomes = [(call.report.when, call.report.outcome) for call in rec.getcalls("pytest_report_teststatus")]
        assert outcomes == [
            ("setup", "skipped"),
            ("teardown", "passed"),
        ]

        assert len(rec.getcalls("pytest_pyfunc_call")) == 0  # test function is not called

    def test_quarantine_fail_setup(self):
        self.testdir.makepyfile(test_fail_setup_quarantined=_TEST_FAIL_SETUP_QUARANTINED)

        rec = self.inline_run("--ddtrace", "-q", extra_env={"_DD_TEST_SKIP_QUARANTINED_TESTS": "true"})

        assert rec.ret == 0
        assert_stats(rec, skipped=1)

        assert len(self.pop_spans()) > 0

    def test_quarantine_fail_teardown(self):
        self.testdir.makepyfile(test_fail_teardown_quarantined=_TEST_FAIL_TEARDOWN_QUARANTINED)

        rec = self.inline_run("--ddtrace", "-q", extra_env={"_DD_TEST_SKIP_QUARANTINED_TESTS": "true"})

        assert rec.ret == 0
        assert_stats(rec, skipped=1)

        assert len(self.pop_spans()) > 0

    def test_quarantine_skipping_spans_atr_disabled(self):
        return self._test_quarantine_skipping_spans(atr_enabled=False)

    def test_quarantine_skipping_spans_atr_enabled(self):
        return self._test_quarantine_skipping_spans(atr_enabled=True)

    def _test_quarantine_skipping_spans(self, atr_enabled):
        # ATR should not affect spans for skipped quarantined tests.
        self.testdir.makepyfile(test_pass_quarantined=_TEST_PASS_QUARANTINED)
        self.testdir.makepyfile(test_fail_quarantined=_TEST_FAIL_QUARANTINED)
        self.testdir.makepyfile(test_fail_setup_quarantined=_TEST_FAIL_SETUP_QUARANTINED)
        self.testdir.makepyfile(test_fail_teardown_quarantined=_TEST_FAIL_TEARDOWN_QUARANTINED)

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(
                test_management=TestManagementSettings(enabled=True),
                flaky_test_retries_enabled=atr_enabled,
            ),
        ):
            rec = self.inline_run("--ddtrace", "-q", extra_env={"_DD_TEST_SKIP_QUARANTINED_TESTS": "true"})

        assert rec.ret == 0
        assert_stats(rec, skipped=4)

        spans = self.pop_spans()

        [session_span] = _get_spans_from_list(spans, "session")
        assert session_span.get_tag("test.test_management.enabled") == "true"

        [module_span] = _get_spans_from_list(spans, "module")
        [suite_span_fail_quarantined] = _get_spans_from_list(spans, "suite", "test_fail_quarantined.py")
        [suite_span_pass_quarantined] = _get_spans_from_list(spans, "suite", "test_pass_quarantined.py")

        [test_span_fail_quarantined] = _get_spans_from_list(spans, "test", "test_fail_quarantined")
        assert test_span_fail_quarantined.get_tag("test.test_management.is_quarantined") == "true"
        assert test_span_fail_quarantined.get_tag("test.status") == "skip"

        [test_span_pass_quarantined] = _get_spans_from_list(spans, "test", "test_pass_quarantined")
        assert test_span_pass_quarantined.get_tag("test.test_management.is_quarantined") == "true"
        assert test_span_pass_quarantined.get_tag("test.status") == "skip"

        [test_span_fail_setup] = _get_spans_from_list(spans, "test", "test_fail_setup")
        assert test_span_fail_setup.get_tag("test.test_management.is_quarantined") == "true"
        assert test_span_fail_setup.get_tag("test.status") == "skip"

        [test_span_fail_teardown] = _get_spans_from_list(spans, "test", "test_fail_teardown")
        assert test_span_fail_teardown.get_tag("test.test_management.is_quarantined") == "true"
        assert test_span_fail_teardown.get_tag("test.status") == "skip"
