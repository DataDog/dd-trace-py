import pytest
from pathlib import Path

from ddtrace.ext.test_visibility._test_visibility_base import TestId
from ddtrace.ext.test_visibility._test_visibility_base import TestModuleId
from ddtrace.ext.test_visibility._test_visibility_base import TestSuiteId
from ddtrace.ext.test_visibility.status import TestStatus
from ddtrace.internal.ci_visibility.api._base import TestVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api._test import TestVisibilityTest
from ddtrace.internal.ci_visibility.telemetry.constants import TEST_FRAMEWORKS
from tests.utils import DummyTracer


def _get_default_civisibility_settings():
    return TestVisibilitySessionSettings(
        tracer=DummyTracer(),
        test_service="test_service",
        test_command="test_command",
        test_framework="test_framework",
        test_framework_version="1.2.3",
        test_framework_metric_name=TEST_FRAMEWORKS.MANUAL,
        session_operation_name="session_operation_name",
        module_operation_name="module_operation_name",
        suite_operation_name="suite_operation_name",
        test_operation_name="test_operation_name",
        workspace_path=Path("/absolute/path/to/root_dir"),
    )


def _get_test():
    return TestVisibilityTest("test_name", _get_default_civisibility_settings())


class TestTwoPhaseFinish:
    """Test the two-phase finish refactor: prepare_for_finish() + write_test()"""

    def test_prepare_for_finish_sets_finish_time(self):
        """Test that prepare_for_finish() sets the finish time but doesn't finish the span"""
        test = _get_test()
        test.start()
        
        # Before prepare_for_finish, test should not be finished
        assert not test.is_finished()
        assert test._finish_time is None
        
        # Call prepare_for_finish
        test.prepare_for_finish(status=TestStatus.PASS)
        
        # After prepare_for_finish, test should be considered finished but span not sent
        assert test.is_finished()
        assert test._finish_time is not None
        assert test._span is not None
        assert not test._span.finished  # Span not actually sent yet
        assert test.get_status() == TestStatus.PASS

    def test_write_test_sends_span(self):
        """Test that write_test() actually sends the span after prepare_for_finish()"""
        test = _get_test()
        test.start()
        test.prepare_for_finish(status=TestStatus.PASS)
        
        # Before write_test, span exists but not finished
        assert test._span is not None
        assert not test._span.finished
        
        # Call write_test
        test.write_test()
        
        # After write_test, span should be finished
        assert test._span.finished

    def test_backward_compatibility_finish_without_prepare_for_finish(self):
        """Test that finish() still works without calling prepare_for_finish() first"""
        test = _get_test()
        test.start()
        
        # Call finish() directly (old style)
        test.finish()
        
        # Should work and finish the test
        assert test.is_finished()
        assert test._finish_time is not None
        assert test._span.finished

    def test_calling_prepare_for_finish_twice_is_safe(self):
        """Test that calling prepare_for_finish() twice doesn't cause issues"""
        test = _get_test()
        test.start()
        
        # First call
        test.prepare_for_finish(status=TestStatus.PASS)
        first_finish_time = test._finish_time
        first_status = test.get_status()
        
        # Second call with different status (should be ignored due to existing logic)
        test.prepare_for_finish(status=TestStatus.FAIL)
        second_finish_time = test._finish_time
        
        # Status should remain the same (first status wins), finish time may be updated
        assert test.get_status() == first_status  # Status doesn't change
        assert second_finish_time >= first_finish_time  # Time may be updated

    def test_write_test_without_prepare_for_finish_fails_gracefully(self):
        """Test that write_test() without prepare_for_finish() works (calls it internally)"""
        test = _get_test()
        test.start()
        
        # Call write_test without prepare_for_finish
        test.write_test()
        
        # Should still work because finish() has backward compatibility
        assert test._span.finished

    def test_status_and_skip_reason_preserved(self):
        """Test that status and skip_reason are properly preserved through the two phases"""
        test = _get_test()
        test.start()
        
        skip_reason = "Test was skipped for a reason"
        test.prepare_for_finish(status=TestStatus.SKIP, skip_reason=skip_reason)
        
        # Status and tags should be set
        assert test.get_status() == TestStatus.SKIP
        assert test.get_tag("test.skip_reason") == skip_reason
        
        # Write the test
        test.write_test()
        
        # Should still have the same status and skip reason
        assert test.get_status() == TestStatus.SKIP
        assert test._span.get_tag("test.skip_reason") == skip_reason

    def test_override_finish_time_works(self):
        """Test that override_finish_time parameter works in prepare_for_finish()"""
        test = _get_test()
        test.start()
        
        custom_finish_time = 1234567890.0
        test.prepare_for_finish(status=TestStatus.PASS, override_finish_time=custom_finish_time)
        
        assert test._finish_time == custom_finish_time

    def test_status_cannot_be_changed_after_first_prepare_for_finish(self):
        """Test that status cannot be changed after first prepare_for_finish() call"""
        test = _get_test()
        test.start()
        
        # Start with PASS
        test.prepare_for_finish(status=TestStatus.PASS)
        assert test.get_status() == TestStatus.PASS
        
        # Attempt to change to FAIL (should be ignored)
        test.prepare_for_finish(status=TestStatus.FAIL)
        assert test.get_status() == TestStatus.PASS  # Should remain PASS
        
        # Attempt to change to SKIP (should be ignored)
        test.prepare_for_finish(status=TestStatus.SKIP, skip_reason="Changed mind")
        assert test.get_status() == TestStatus.PASS  # Should remain PASS
        
        # Write and verify final status
        test.write_test()
        assert test._span.get_tag("test.status") == TestStatus.PASS.value

    def test_test_is_considered_finished_after_prepare_for_finish(self):
        """Test that is_finished() returns True after prepare_for_finish() even if span not sent"""
        test = _get_test()
        test.start()
        
        assert not test.is_finished()
        
        test.prepare_for_finish(status=TestStatus.PASS)
        
        # Should be considered finished even though span not sent yet
        assert test.is_finished()
        
        test.write_test()
        
        # Should still be finished
        assert test.is_finished()

    def test_retry_logic_with_prepare_for_finish(self):
        """Test that retry decision logic works with prepare_for_finish()"""
        test = _get_test()
        test.start()
        
        # Prepare for finish but don't write yet
        test.prepare_for_finish(status=TestStatus.FAIL)
        
        # Should be able to check if retry is needed
        # (This tests that _finish_time is set, which retry logic depends on)
        assert test._finish_time is not None
        
        # Write the test
        test.write_test()
        
        assert test._span.finished

    def test_finish_calls_prepare_for_finish_if_not_called(self):
        """Test that finish() calls prepare_for_finish() internally if not called yet"""
        test = _get_test()
        test.start()
        
        # Call finish() directly without prepare_for_finish()
        test.finish()
        
        # Should automatically call prepare_for_finish() internally
        assert test.is_finished()
        assert test._finish_time is not None
        assert test._span.finished