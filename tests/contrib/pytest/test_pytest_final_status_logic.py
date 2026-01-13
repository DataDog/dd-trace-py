"""Tests for final_status tag on test spans.

These tests verify that the final_status tag is correctly set on test spans
for different retry scenarios (ATR, EFD, Attempt to Fix) and tests without retries.
"""

from unittest import mock

import pytest

from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from tests.contrib.pytest.test_pytest import PytestTestCaseBase
from tests.contrib.pytest.test_pytest import _get_spans_from_list


class TestFinalStatusTag(PytestTestCaseBase):
    """Test that final_status tag is set correctly on test spans."""

    @pytest.fixture(autouse=True, scope="function")
    def set_up_atr(self):
        """Enable ATR for tests that need retries."""
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(flaky_test_retries_enabled=True),
        ):
            yield

    def test_final_status_on_passing_test(self):
        """Test that passing tests without retries have final_status='pass'."""
        py_file = self.testdir.makepyfile(
            """
            def test_will_pass():
                assert True
        """
        )
        file_name = py_file.basename.split(".")[0]
        rec = self.inline_run("--ddtrace")
        assert rec.ret == 0

        spans = self.pop_spans()
        test_spans = [s for s in spans if s.get_tag("type") == "test"]
        assert len(test_spans) == 1

        test_span = test_spans[0]
        assert test_span.get_tag("test.name") == "test_will_pass"
        assert test_span.get_tag("test.status") == "pass"
        assert test_span.get_tag("test.final_status") == "pass"

    def test_final_status_on_failing_test(self):
        """Test that failing tests with ATR have final_status='fail' on last retry."""
        py_file = self.testdir.makepyfile(
            """
            def test_will_fail():
                assert False
        """
        )
        file_name = py_file.basename.split(".")[0]
        rec = self.inline_run("--ddtrace")
        assert rec.ret == 1

        spans = self.pop_spans()
        test_spans = [s for s in spans if s.get_tag("type") == "test"]
        # ATR retries failing tests (default 5 retries + 1 initial = 6 total)
        assert len(test_spans) == 6

        # First 5 attempts should NOT have final_status
        for i in range(5):
            assert test_spans[i].get_tag("test.status") == "fail"
            assert test_spans[i].get_tag("test.final_status") is None, f"Retry {i+1} should not have final_status tag"

        # Last retry should have final_status='fail'
        last_span = test_spans[5]
        assert last_span.get_tag("test.name") == "test_will_fail"
        assert last_span.get_tag("test.status") == "fail"
        assert last_span.get_tag("test.final_status") == "fail"

    def test_final_status_on_skipped_test(self):
        """Test that skipped tests without retries have final_status='skip'."""
        py_file = self.testdir.makepyfile(
            """
            import pytest
            
            @pytest.mark.skip(reason="test")
            def test_will_skip():
                assert True
        """
        )
        file_name = py_file.basename.split(".")[0]
        rec = self.inline_run("--ddtrace")
        assert rec.ret == 0

        spans = self.pop_spans()
        test_spans = [s for s in spans if s.get_tag("type") == "test"]
        assert len(test_spans) == 1

        test_span = test_spans[0]
        assert test_span.get_tag("test.name") == "test_will_skip"
        assert test_span.get_tag("test.status") == "skip"
        assert test_span.get_tag("test.final_status") == "skip"

    def test_final_status_on_atr_all_fail(self):
        """Test that ATR sets final_status='fail' when all retries fail."""
        py_file = self.testdir.makepyfile(
            """
            def test_atr_all_fail():
                assert False, "Always fails"
        """
        )
        file_name = py_file.basename.split(".")[0]
        rec = self.inline_run("--ddtrace")
        assert rec.ret == 1

        spans = self.pop_spans()
        test_spans = [s for s in spans if s.get_tag("type") == "test"]
        # Default ATR retries: should have 6 test spans (1 initial + 5 retries)
        assert len(test_spans) == 6

        # First 5 retries should NOT have final_status
        for i in range(5):
            assert test_spans[i].get_tag("test.status") == "fail"
            assert test_spans[i].get_tag("test.final_status") is None, f"Retry {i+1} should not have final_status tag"

        # Last retry should have final_status='fail'
        last_span = test_spans[5]
        assert last_span.get_tag("test.status") == "fail"
        assert last_span.get_tag("test.final_status") == "fail"
        assert last_span.get_tag("test.has_failed_all_retries") == "true"

