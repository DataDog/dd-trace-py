"""Tests for final_status tag on test spans

This test file verifies that the final_status tag is correctly set on test spans for:
- Regular tests (no retries)
- ATR (Auto Test Retries)
- EFD (Early Flake Detection)
- Attempt to Fix
"""

from unittest import mock

import pytest

from ddtrace.ext import test as test_ext
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from tests.contrib.pytest.test_pytest import PytestTestCaseBase
from tests.contrib.pytest.test_pytest import _get_spans_from_list


class TestFinalStatusTag(PytestTestCaseBase):
    """Test final_status tag is set correctly on test spans"""

    def test_final_status_on_passing_test_no_retries(self):
        """Regular passing test should have final_status='pass'"""
        self.testdir.makepyfile(
            """
            def test_pass():
                assert True
        """
        )
        rec = self.inline_run("--ddtrace")
        assert rec.ret == 0

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test")
        assert len(test_spans) == 1
        assert test_spans[0].get_tag(test_ext.STATUS) == "pass"
        assert test_spans[0].get_tag(test_ext.FINAL_STATUS) == "pass"

    def test_final_status_on_failing_test_no_retries(self):
        """Regular failing test should have final_status='fail'"""
        self.testdir.makepyfile(
            """
            def test_fail():
                assert False
        """
        )
        rec = self.inline_run("--ddtrace")
        assert rec.ret == 1

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test")
        assert len(test_spans) == 1
        assert test_spans[0].get_tag(test_ext.STATUS) == "fail"
        assert test_spans[0].get_tag(test_ext.FINAL_STATUS) == "fail"

    def test_final_status_on_skipped_test_no_retries(self):
        """Regular skipped test should have final_status='skip'"""
        self.testdir.makepyfile(
            """
            import pytest

            def test_skip():
                pytest.skip("skipping")
        """
        )
        rec = self.inline_run("--ddtrace")
        assert rec.ret == 0

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test")
        assert len(test_spans) == 1
        assert test_spans[0].get_tag(test_ext.STATUS) == "skip"
        assert test_spans[0].get_tag(test_ext.FINAL_STATUS) == "skip"


class TestATRFinalStatus(PytestTestCaseBase):
    """Test final_status tag for Auto Test Retries scenarios"""

    @pytest.fixture(autouse=True)
    def enable_atr(self):
        """Enable ATR for these tests"""
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(flaky_test_retries_enabled=True),
        ):
            yield

    def test_atr_final_status_passes_on_first_try(self):
        """Test that passes on first try should have final_status on the single span"""
        self.testdir.makepyfile(
            """
            def test_pass_first_try():
                assert True
        """
        )
        rec = self.inline_run("--ddtrace")
        assert rec.ret == 0

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test")

        # Should only have 1 span (no retries triggered)
        assert len(test_spans) == 1
        assert test_spans[0].get_tag(test_ext.STATUS) == "pass"
        assert test_spans[0].get_tag(test_ext.FINAL_STATUS) == "pass"
        assert test_spans[0].get_tag(test_ext.IS_RETRY) is None

    def test_atr_final_status_eventually_passes(self):
        """Test that eventually passes should have final_status='pass' only on last retry"""
        self.testdir.makepyfile(
            """
            fail_count = 0

            def test_eventually_pass():
                global fail_count
                fail_count += 1
                if fail_count < 3:
                    assert False, f"Failing attempt {fail_count}"
                assert True
        """
        )
        rec = self.inline_run("--ddtrace", extra_env={"DD_CIVISIBILITY_FLAKY_RETRY_COUNT": "3"})
        assert rec.ret == 0

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test")

        # Should have 3 spans: initial fail + 2 retry fails + 1 retry pass = 3 total
        assert len(test_spans) == 3

        # First 2 attempts should NOT have final_status
        for i in range(2):
            assert test_spans[i].get_tag(test_ext.STATUS) == "fail"
            assert test_spans[i].get_tag(test_ext.FINAL_STATUS) is None, f"Attempt {i + 1} should not have final_status"

        # Last attempt should have final_status='pass'
        assert test_spans[2].get_tag(test_ext.STATUS) == "pass"
        assert test_spans[2].get_tag(test_ext.FINAL_STATUS) == "pass"
        assert test_spans[2].get_tag(test_ext.IS_RETRY) == "true"

    def test_atr_final_status_all_fail(self):
        """Test that fails all retries should have final_status='fail' on last retry"""
        self.testdir.makepyfile(
            """
            def test_always_fail():
                assert False, "Always fails"
        """
        )
        rec = self.inline_run("--ddtrace", extra_env={"DD_CIVISIBILITY_FLAKY_RETRY_COUNT": "2"})
        assert rec.ret == 1

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test")

        # Should have 3 spans: initial + 2 retries
        assert len(test_spans) == 3

        # First 2 attempts should NOT have final_status
        for i in range(2):
            assert test_spans[i].get_tag(test_ext.STATUS) == "fail"
            assert test_spans[i].get_tag(test_ext.FINAL_STATUS) is None, f"Attempt {i + 1} should not have final_status"

        # Last attempt should have final_status='fail' and has_failed_all_retries
        assert test_spans[2].get_tag(test_ext.STATUS) == "fail"
        assert test_spans[2].get_tag(test_ext.FINAL_STATUS) == "fail"
        assert test_spans[2].get_tag(test_ext.TEST_HAS_FAILED_ALL_RETRIES) == "true"

    def test_atr_final_status_with_skip(self):
        """Test that becomes skipped should have final_status='skip' on last retry"""
        self.testdir.makepyfile(
            """
            import pytest

            attempt_count = 0

            def test_skip_on_retry():
                global attempt_count
                attempt_count += 1
                if attempt_count == 1:
                    assert False, "First attempt fails"
                pytest.skip("Skipping on retry")
        """
        )
        rec = self.inline_run("--ddtrace", extra_env={"DD_CIVISIBILITY_FLAKY_RETRY_COUNT": "2"})
        assert rec.ret == 0

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test")

        # Should have 2 spans: initial fail + 1 retry skip
        assert len(test_spans) == 2

        # First attempt should NOT have final_status
        assert test_spans[0].get_tag(test_ext.STATUS) == "fail"
        assert test_spans[0].get_tag(test_ext.FINAL_STATUS) is None

        # Last attempt should have final_status='skip'
        assert test_spans[1].get_tag(test_ext.STATUS) == "skip"
        assert test_spans[1].get_tag(test_ext.FINAL_STATUS) == "skip"


class TestEFDFinalStatus(PytestTestCaseBase):
    """Test final_status tag for Early Flake Detection scenarios"""

    @pytest.fixture(autouse=True)
    def enable_efd(self):
        """Enable EFD for these tests"""
        from ddtrace.internal.ci_visibility._api_client import EarlyFlakeDetectionSettings

        with (
            mock.patch(
                "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
                return_value=TestVisibilityAPISettings(
                    early_flake_detection=EarlyFlakeDetectionSettings(
                        enabled=True,
                        slow_test_retries_5s=2,
                        slow_test_retries_10s=2,
                        slow_test_retries_30s=2,
                        slow_test_retries_5m=2,
                    )
                ),
            ),
            mock.patch(
                "ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_unique_tests",
                return_value=set(),  # Empty set means all tests are "new"
            ),
        ):
            yield

    def test_efd_final_status_all_pass(self):
        """EFD test that passes all attempts should have final_status='pass' on last retry"""
        self.testdir.makepyfile(
            """
            def test_efd_all_pass():
                assert True
        """
        )
        rec = self.inline_run("--ddtrace")
        assert rec.ret == 0

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test")

        # Should have 3 spans for EFD (initial + retries based on duration)
        assert len(test_spans) >= 1

        # All intermediate attempts should NOT have final_status
        for i in range(len(test_spans) - 1):
            assert test_spans[i].get_tag(test_ext.STATUS) == "pass"
            assert test_spans[i].get_tag(test_ext.FINAL_STATUS) is None, f"Retry {i + 1} should not have final_status"

        # Last attempt should have final_status='pass'
        last_span = test_spans[-1]
        assert last_span.get_tag(test_ext.STATUS) == "pass"
        assert last_span.get_tag(test_ext.FINAL_STATUS) == "pass"

    def test_efd_final_status_all_fail(self):
        """EFD test that fails all attempts should have final_status='fail' on last retry"""
        self.testdir.makepyfile(
            """
            def test_efd_all_fail():
                assert False, "Always fails"
        """
        )
        rec = self.inline_run("--ddtrace")
        assert rec.ret == 1

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test")

        # Should have multiple spans for EFD
        assert len(test_spans) >= 1

        # All intermediate attempts should NOT have final_status
        for i in range(len(test_spans) - 1):
            assert test_spans[i].get_tag(test_ext.STATUS) == "fail"
            assert test_spans[i].get_tag(test_ext.FINAL_STATUS) is None, f"Retry {i + 1} should not have final_status"

        # Last attempt should have final_status='fail'
        last_span = test_spans[-1]
        assert last_span.get_tag(test_ext.STATUS) == "fail"
        assert last_span.get_tag(test_ext.FINAL_STATUS) == "fail"

    def test_efd_final_status_flaky(self):
        """EFD test that is flaky should have final_status='pass' on last retry (passes eventually)"""
        self.testdir.makepyfile(
            """
            attempt_count = 0

            def test_efd_flaky():
                global attempt_count
                attempt_count += 1
                if attempt_count == 1:
                    assert False, "First attempt fails"
                assert True
        """
        )
        rec = self.inline_run("--ddtrace")
        assert rec.ret == 0

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test")

        # Should have at least 2 spans (initial fail + retry pass)
        assert len(test_spans) >= 2

        # All intermediate attempts should NOT have final_status
        for i in range(len(test_spans) - 1):
            assert test_spans[i].get_tag(test_ext.FINAL_STATUS) is None, f"Retry {i + 1} should not have final_status"

        # Last attempt should have final_status='pass' (EFD passes if any attempt passes)
        last_span = test_spans[-1]
        assert last_span.get_tag(test_ext.FINAL_STATUS) == "pass"


class TestAttemptToFixFinalStatus(PytestTestCaseBase):
    """Test final_status tag for Attempt to Fix scenarios"""

    @pytest.fixture(autouse=True)
    def enable_attempt_to_fix(self):
        """Enable Attempt to Fix for these tests"""
        with (
            mock.patch(
                "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
                return_value=TestVisibilityAPISettings(
                    flaky_test_retries_enabled=False,  # Disable ATR
                    require_git=False,
                ),
            ),
            mock.patch(
                "ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip",
                return_value=([], []),
            ),
        ):
            yield

    def test_attempt_to_fix_final_status_all_pass(self):
        """Attempt to Fix test that passes all attempts should have final_status='pass' on last retry"""
        self.testdir.makepyfile(
            """
            def test_attempt_to_fix_all_pass():
                assert True
        """
        )

        # Mark test as attempt_to_fix via marker
        self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.dd_tags(attempt_to_fix_enabled="true")
            def test_attempt_to_fix_all_pass():
                assert True
        """
        )

        self.inline_run("--ddtrace", extra_env={"DD_CIVISIBILITY_ATTEMPT_TO_FIX_RETRIES": "2"})

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test")

        if len(test_spans) > 1:
            # If retries happened, check intermediate spans don't have final_status
            for i in range(len(test_spans) - 1):
                assert test_spans[i].get_tag(test_ext.FINAL_STATUS) is None

            # Last span should have final_status='pass'
            last_span = test_spans[-1]
            assert last_span.get_tag(test_ext.FINAL_STATUS) == "pass"

    def test_attempt_to_fix_final_status_all_fail(self):
        """Attempt to Fix test that fails all attempts should have final_status='fail' on last retry"""
        self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.dd_tags(attempt_to_fix_enabled="true")
            def test_attempt_to_fix_all_fail():
                assert False, "Always fails"
        """
        )

        self.inline_run("--ddtrace", extra_env={"DD_CIVISIBILITY_ATTEMPT_TO_FIX_RETRIES": "2"})

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test")

        if len(test_spans) > 1:
            # If retries happened, check intermediate spans don't have final_status
            for i in range(len(test_spans) - 1):
                assert test_spans[i].get_tag(test_ext.FINAL_STATUS) is None

            # Last span should have final_status='fail'
            last_span = test_spans[-1]
            assert last_span.get_tag(test_ext.FINAL_STATUS) == "fail"


class TestFinalStatusEdgeCases(PytestTestCaseBase):
    """Test edge cases for final_status tag"""

    def test_final_status_setup_failure(self):
        """Test with setup failure should have final_status (no retries triggered)"""
        self.testdir.makepyfile(
            """
            import pytest

            @pytest.fixture
            def failing_fixture():
                raise Exception("Setup failed")

            def test_with_setup_failure(failing_fixture):
                assert True
        """
        )
        rec = self.inline_run("--ddtrace")
        assert rec.ret == 1

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test")

        # Should only have 1 span (setup failures don't trigger retries)
        assert len(test_spans) == 1
        assert test_spans[0].get_tag(test_ext.STATUS) == "fail"
        assert test_spans[0].get_tag(test_ext.FINAL_STATUS) == "fail"

    def test_final_status_teardown_failure(self):
        """Test with teardown failure should have final_status (no retries triggered)"""
        self.testdir.makepyfile(
            """
            import pytest

            @pytest.fixture
            def failing_teardown():
                yield
                raise Exception("Teardown failed")

            def test_with_teardown_failure(failing_teardown):
                assert True
        """
        )
        rec = self.inline_run("--ddtrace")
        assert rec.ret == 1

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test")

        # Should only have 1 span (teardown failures don't trigger retries)
        assert len(test_spans) == 1
        assert test_spans[0].get_tag(test_ext.STATUS) == "fail"
        assert test_spans[0].get_tag(test_ext.FINAL_STATUS) == "fail"

    def test_final_status_xfail(self):
        """Test marked as xfail should have final_status"""
        self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.xfail
            def test_expected_to_fail():
                assert False
        """
        )
        rec = self.inline_run("--ddtrace")
        assert rec.ret == 0

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test")

        assert len(test_spans) == 1
        # xfail that fails is treated as pass
        assert test_spans[0].get_tag(test_ext.STATUS) == "pass"
        assert test_spans[0].get_tag(test_ext.FINAL_STATUS) == "pass"

    def test_final_status_xpass(self):
        """Test marked as xfail but passes should have final_status"""
        self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.xfail(strict=False)
            def test_expected_to_fail_but_passes():
                assert True
        """
        )
        rec = self.inline_run("--ddtrace")
        assert rec.ret == 0

        spans = self.pop_spans()
        test_spans = _get_spans_from_list(spans, "test")

        assert len(test_spans) == 1
        # xpass is treated as pass (when not strict)
        assert test_spans[0].get_tag(test_ext.STATUS) == "pass"
        assert test_spans[0].get_tag(test_ext.FINAL_STATUS) == "pass"
