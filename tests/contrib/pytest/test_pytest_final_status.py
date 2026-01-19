"""Tests for the test.final_status tag feature.

This module tests that the test.final_status tag is correctly set on the last retry
of a test for these retry mechanisms: ATR, Attempt to Fix and for tests without retries.

The final_status tag should be present on every final execution of a test, either because
there are no retries or because it's the last retry.
"""

from unittest import mock

import pytest

from ddtrace.contrib.internal.pytest._utils import _pytest_version_supports_atr
from ddtrace.contrib.internal.pytest._utils import _pytest_version_supports_attempt_to_fix
from ddtrace.internal.ci_visibility._api_client import TestManagementSettings
from ddtrace.internal.ci_visibility._api_client import TestProperties
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from tests.ci_visibility.api_client._util import _make_fqdn_internal_test_id
from tests.contrib.pytest.test_pytest import PytestTestCaseBase
from tests.contrib.pytest.test_pytest import _get_spans_from_list


# Test content definitions
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
    pytest.skip("skipping test")
"""

_TEST_PASS_ON_RETRY_2 = """
count = 0

def test_pass_on_retry_2():
    global count
    count += 1
    assert count == 2
"""

_TEST_SKIP_ON_RETRY_THEN_PASS = """
count = 0

def test_skip_on_retry_then_pass():
    import pytest
    global count
    count += 1
    if count == 2:
        pytest.skip("skipping on retry 1")
    if count < 3:
        assert False
    assert True
"""

_TEST_SKIP_ALL_RETRIES = """
import pytest

count = 0

def test_skip_all_retries():
    global count
    count += 1
    if count > 1:
        pytest.skip("skipping on retries")
    assert False
"""


class TestFinalStatusNoRetries(PytestTestCaseBase):
    """Test that final_status is set correctly for tests without any retry mechanisms."""

    def test_final_status_pass_no_retries(self):
        """Test that passes on first attempt should have final_status:pass"""
        self.testdir.makepyfile(test_simple=_TEST_PASS)
        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 0

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test", "test_pass")
        assert len(test_spans) == 1

        final_span = test_spans[0]
        assert final_span.get_tag("test.status") == "pass"
        assert final_span.get_tag("test.final_status") == "pass"

    def test_final_status_fail_no_retries(self):
        """Test that fails on first attempt should have final_status:fail"""
        self.testdir.makepyfile(test_simple=_TEST_FAIL)
        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 1

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test", "test_fail")
        assert len(test_spans) == 1

        final_span = test_spans[0]
        assert final_span.get_tag("test.status") == "fail"
        assert final_span.get_tag("test.final_status") == "fail"

    def test_final_status_skip_no_retries(self):
        """Test that skips on first attempt should have final_status:skip"""
        self.testdir.makepyfile(test_simple=_TEST_SKIP)
        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 0

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test", "test_skip")
        assert len(test_spans) == 1

        final_span = test_spans[0]
        assert final_span.get_tag("test.status") == "skip"
        assert final_span.get_tag("test.final_status") == "skip"


@pytest.mark.skipif(not _pytest_version_supports_atr(), reason="ATR tests require pytest >=7.0")
class TestFinalStatusATR(PytestTestCaseBase):
    """Test that final_status is set correctly for ATR (Adaptive Test Retry) scenarios."""

    @pytest.fixture(autouse=True, scope="function")
    def set_up_atr(self):
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(flaky_test_retries_enabled=True),
        ):
            yield

    def test_atr_final_status_fail_then_pass(self):
        """ATR: Test fails first, passes on retry → final_status:pass"""
        self.testdir.makepyfile(test_atr=_TEST_PASS_ON_RETRY_2)
        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 0  # Should pass because retry passes

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test", "test_pass_on_retry_2")
        assert len(test_spans) == 2  # Original + 1 retry

        # Check that only the last span has final_status tag
        first_span = test_spans[0]
        assert first_span.get_tag("test.status") == "fail"
        assert first_span.get_tag("test.final_status") is None  # No final_status on intermediate

        final_span = test_spans[-1]
        assert final_span.get_tag("test.status") == "pass"
        assert final_span.get_tag("test.final_status") == "pass"
        assert final_span.get_tag("test.is_retry") == "true"

    def test_atr_final_status_all_fail(self):
        """ATR: Test fails all retries → final_status:fail"""
        self.testdir.makepyfile(test_atr=_TEST_FAIL)
        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 1  # Should fail because all retries fail

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test", "test_fail")
        assert len(test_spans) == 6  # Original + 5 retries (default)

        # Verify spans are in chronological order by start time
        for i in range(len(test_spans) - 1):
            assert test_spans[i].start < test_spans[i + 1].start, (
                f"Spans not in chronological order: span {i} starts at or after span {i + 1}"
            )

        # Check that only the last span has final_status tag
        for span in test_spans[:-1]:
            assert span.get_tag("test.status") == "fail"
            assert span.get_tag("test.final_status") is None  # No final_status on intermediate

        final_span = test_spans[-1]
        assert final_span.get_tag("test.status") == "fail"
        assert final_span.get_tag("test.final_status") == "fail"
        assert final_span.get_tag("test.is_retry") == "true"

    def test_atr_final_status_skip_no_retries(self):
        """ATR: Test skips on first attempt → final_status:skip (no retries for skip)"""
        self.testdir.makepyfile(test_atr=_TEST_SKIP)
        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 0

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test", "test_skip")
        assert len(test_spans) == 1  # No retries for skip

        final_span = test_spans[0]
        assert final_span.get_tag("test.status") == "skip"
        assert final_span.get_tag("test.final_status") == "skip"

    def test_atr_final_status_fail_skip_pass(self):
        """ATR: Test fails, skips on retry, then passes → final_status:pass"""
        self.testdir.makepyfile(test_atr=_TEST_SKIP_ON_RETRY_THEN_PASS)
        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 0  # Should pass because eventually passes

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test", "test_skip_on_retry_then_pass")
        assert len(test_spans) == 3  # Original fail, skip retry, pass retry

        # Check intermediate spans don't have final_status
        assert test_spans[0].get_tag("test.status") == "fail"
        assert test_spans[0].get_tag("test.final_status") is None

        assert test_spans[1].get_tag("test.status") == "skip"
        assert test_spans[1].get_tag("test.final_status") is None

        # Check final span has final_status:pass
        final_span = test_spans[-1]
        assert final_span.get_tag("test.status") == "pass"
        assert final_span.get_tag("test.final_status") == "pass"

    def test_atr_final_status_fail_then_skip_all(self):
        """ATR: Test fails, then skips on all retries → final_status:fail"""
        self.testdir.makepyfile(test_atr=_TEST_SKIP_ALL_RETRIES)
        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 1  # Should fail because never passes

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test", "test_skip_all_retries")
        assert len(test_spans) == 6  # Original fail + 5 skip retries

        # Check that only the last span has final_status tag
        for span in test_spans[:-1]:
            assert span.get_tag("test.final_status") is None

        final_span = test_spans[-1]
        assert final_span.get_tag("test.status") == "skip"
        assert final_span.get_tag("test.final_status") == "fail"  # Final status is fail, not skip


@pytest.mark.skipif(not _pytest_version_supports_attempt_to_fix(), reason="Attempt to Fix tests require pytest >=7.0")
class TestFinalStatusAttemptToFix(PytestTestCaseBase):
    """Test that final_status is set correctly for Attempt to Fix scenarios."""

    _TEST_PROPERTIES = {
        _make_fqdn_internal_test_id("", "test_quarantined.py", "test_pass"): TestProperties(
            quarantined=True,
            attempt_to_fix=True,
        ),
        _make_fqdn_internal_test_id("", "test_quarantined.py", "test_fail"): TestProperties(
            quarantined=True,
            attempt_to_fix=True,
        ),
        _make_fqdn_internal_test_id("", "test_quarantined.py", "test_skip"): TestProperties(
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
        _make_fqdn_internal_test_id("", "test_disabled.py", "test_fail"): TestProperties(
            disabled=True,
            attempt_to_fix=True,
        ),
    }

    @pytest.fixture(autouse=True, scope="function")
    def set_up_attempt_to_fix(self):
        with (
            mock.patch(
                "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
                return_value=TestVisibilityAPISettings(
                    test_management=TestManagementSettings(
                        enabled=True,
                        attempt_to_fix_retries=10,
                    ),
                    flaky_test_retries_enabled=False,
                ),
            ),
            mock.patch(
                "ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_test_management_tests",
                return_value=self._TEST_PROPERTIES,
            ),
        ):
            yield

    def test_attempt_to_fix_final_status_quarantined_pass(self):
        """Attempt to Fix: Quarantined test passes all attempts → final_status:pass"""
        self.testdir.makepyfile(test_quarantined=_TEST_PASS)
        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 0

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test", "test_pass")
        assert len(test_spans) == 11  # Original + 10 retries

        # Check that only the last span has final_status tag
        for span in test_spans[:-1]:
            assert span.get_tag("test.status") == "pass"
            assert span.get_tag("test.final_status") is None

        final_span = test_spans[-1]
        assert final_span.get_tag("test.status") == "pass"
        assert final_span.get_tag("test.final_status") == "pass"
        assert final_span.get_tag("test.test_management.attempt_to_fix_passed") == "true"

    def test_attempt_to_fix_final_status_quarantined_fail(self):
        """Attempt to Fix: Quarantined test fails all attempts → final_status:fail"""
        self.testdir.makepyfile(test_quarantined=_TEST_FAIL)
        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 0  # Quarantined failures don't fail CI

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test", "test_fail")
        assert len(test_spans) == 11

        # Check that only the last span has final_status tag
        for span in test_spans[:-1]:
            assert span.get_tag("test.status") == "fail"
            assert span.get_tag("test.final_status") is None

        final_span = test_spans[-1]
        assert final_span.get_tag("test.status") == "fail"
        assert final_span.get_tag("test.final_status") == "fail"
        assert final_span.get_tag("test.test_management.attempt_to_fix_passed") == "false"

    def test_attempt_to_fix_final_status_quarantined_skip(self):
        """Attempt to Fix: Quarantined test skips all attempts → final_status:skip"""
        self.testdir.makepyfile(test_quarantined=_TEST_SKIP)
        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 0

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test", "test_skip")
        assert len(test_spans) == 11

        # Check that only the last span has final_status tag
        for span in test_spans[:-1]:
            assert span.get_tag("test.status") == "skip"
            assert span.get_tag("test.final_status") is None

        final_span = test_spans[-1]
        assert final_span.get_tag("test.status") == "skip"
        assert final_span.get_tag("test.final_status") == "skip"

    def test_attempt_to_fix_final_status_quarantined_flaky(self):
        """Attempt to Fix: Quarantined test fails then passes → final_status:fail (all must pass)"""
        test_content = """
count = 0

def test_flaky():
    global count
    count += 1
    assert count == 11  # Pass only on last attempt
"""
        self.testdir.makepyfile(test_quarantined=test_content)
        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 0  # Quarantined

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test", "test_flaky")
        assert len(test_spans) == 11

        # Check that only the last span has final_status tag
        for span in test_spans[:-1]:
            assert span.get_tag("test.final_status") is None

        # Final span passes but overall status is fail because not all passed
        final_span = test_spans[-1]
        assert final_span.get_tag("test.status") == "pass"
        assert final_span.get_tag("test.final_status") == "fail"
        assert final_span.get_tag("test.test_management.attempt_to_fix_passed") == "false"

    def test_attempt_to_fix_final_status_disabled_pass(self):
        """Attempt to Fix: Disabled test passes all attempts → final_status:pass"""
        self.testdir.makepyfile(test_disabled=_TEST_PASS)
        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 0

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test", "test_pass")
        assert len(test_spans) == 11

        # Check that only the last span has final_status tag
        for span in test_spans[:-1]:
            assert span.get_tag("test.final_status") is None

        final_span = test_spans[-1]
        assert final_span.get_tag("test.status") == "pass"
        assert final_span.get_tag("test.final_status") == "pass"

    def test_attempt_to_fix_final_status_disabled_fail(self):
        """Attempt to Fix: Disabled test fails all attempts → final_status:fail"""
        self.testdir.makepyfile(test_disabled=_TEST_FAIL)
        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 0  # Disabled tests don't fail CI

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test", "test_fail")
        assert len(test_spans) == 11

        # Check that only the last span has final_status tag
        for span in test_spans[:-1]:
            assert span.get_tag("test.final_status") is None

        final_span = test_spans[-1]
        assert final_span.get_tag("test.status") == "fail"
        assert final_span.get_tag("test.final_status") == "fail"


class TestFinalStatusMixedScenarios(PytestTestCaseBase):
    """Test final_status behavior in complex scenarios combining multiple retry mechanisms."""

    def test_final_status_mixed_tests(self):
        """Test that final_status is correctly set across multiple tests with different outcomes"""
        self.testdir.makepyfile(test_mixed=_TEST_PASS + "\n" + _TEST_FAIL + "\n" + _TEST_SKIP)
        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 1  # Should fail because test_fail fails

        spans = self.pop_spans()

        # Check pass test
        pass_spans = _get_spans_from_list(spans, "test", "test_pass")
        assert len(pass_spans) == 1
        assert pass_spans[0].get_tag("test.status") == "pass"
        assert pass_spans[0].get_tag("test.final_status") == "pass"

        # Check fail test
        fail_spans = _get_spans_from_list(spans, "test", "test_fail")
        assert len(fail_spans) == 1
        assert fail_spans[0].get_tag("test.status") == "fail"
        assert fail_spans[0].get_tag("test.final_status") == "fail"

        # Check skip test
        skip_spans = _get_spans_from_list(spans, "test", "test_skip")
        assert len(skip_spans) == 1
        assert skip_spans[0].get_tag("test.status") == "skip"
        assert skip_spans[0].get_tag("test.final_status") == "skip"


class TestFinalStatusEdgeCases(PytestTestCaseBase):
    """Test final_status behavior in edge cases and error scenarios."""

    def test_final_status_test_error(self):
        """Test that errors during test execution are treated as failures"""
        test_content = """
def test_error():
    raise RuntimeError("Unexpected error")
"""
        self.testdir.makepyfile(test_edge=test_content)
        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 1

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test", "test_error")
        assert len(test_spans) == 1

        final_span = test_spans[0]
        assert final_span.get_tag("test.status") == "fail"
        assert final_span.get_tag("test.final_status") == "fail"

    def test_final_status_test_setup_error(self):
        """Test that setup errors are treated as failures"""
        test_content = """
import pytest

@pytest.fixture
def broken_fixture():
    raise RuntimeError("Setup failed")

def test_with_broken_fixture(broken_fixture):
    assert True
"""
        self.testdir.makepyfile(test_edge=test_content)
        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 1

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test", "test_with_broken_fixture")
        assert len(test_spans) == 1

        final_span = test_spans[0]
        assert final_span.get_tag("test.status") == "fail"
        assert final_span.get_tag("test.final_status") == "fail"

    def test_final_status_xfail_not_strict(self):
        """Test that xfail tests with strict=False are handled correctly"""
        test_content = """
import pytest

@pytest.mark.xfail(strict=False)
def test_xfail():
    assert False
"""
        self.testdir.makepyfile(test_edge=test_content)
        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 0

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test", "test_xfail")
        assert len(test_spans) == 1

        final_span = test_spans[0]
        # xfail with strict=False that fails marks as pass with result=xfail
        assert final_span.get_tag("test.status") == "pass"
        assert final_span.get_tag("test.final_status") == "pass"

    def test_final_status_xfail_strict(self):
        """Test that xfail tests with strict=True are handled correctly"""
        test_content = """
import pytest

@pytest.mark.xfail(strict=True)
def test_xfail():
    assert False
"""
        self.testdir.makepyfile(test_edge=test_content)
        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 0

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test", "test_xfail")
        assert len(test_spans) == 1

        final_span = test_spans[0]
        # xfail with strict=True that fails marks as pass with result=xfail
        assert final_span.get_tag("test.status") == "pass"
        assert final_span.get_tag("test.final_status") == "pass"

    def test_final_status_parametrized_tests(self):
        """Test that parametrized tests each get their own final_status"""
        test_content = """
import pytest

@pytest.mark.parametrize("value", [1, 2, 3])
def test_parametrized(value):
    assert value != 2  # Only value=2 should fail
"""
        self.testdir.makepyfile(test_edge=test_content)
        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 1  # Should fail because value=2 fails

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test")

        # Should have 3 test spans for the 3 parameter values
        test_parametrized_spans = [s for s in test_spans if "test_parametrized" in s.get_tag("test.name")]
        assert len(test_parametrized_spans) == 3

        # Each should have final_status set
        for span in test_parametrized_spans:
            assert span.get_tag("test.final_status") is not None
            if "[2]" in span.get_tag("test.name"):
                assert span.get_tag("test.final_status") == "fail"
            else:
                assert span.get_tag("test.final_status") == "pass"
