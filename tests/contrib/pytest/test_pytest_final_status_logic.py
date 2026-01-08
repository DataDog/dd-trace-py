"""Tests for final_status tag logic in the old pytest plugin (_plugin_v2.py)

These tests focus on the logic that determines whether the final_status tag should be set
before finishing a test, particularly for EFD duration-based retry decisions.
"""

import mock

from ddtrace.internal.ci_visibility.api._test import TestVisibilityTest


class TestEFDShouldRetryWithRequireFinishedFalse:
    """Test the efd_should_retry(require_finished=False) method with various edge cases."""

    def test_efd_disabled_returns_false(self):
        """When EFD is disabled, should return False regardless of other conditions."""
        mock_test, current_time_ns = self._create_mock_test(
            duration_s=3.0, num_retries=0, retries_5s_limit=5, efd_enabled=False
        )
        result = self._run_test_with_duration(mock_test, current_time_ns)
        assert result is False

    def test_not_new_test_returns_false(self):
        """When test is not new, should return False even if EFD is enabled."""
        mock_test, current_time_ns = self._create_mock_test(
            duration_s=3.0, num_retries=0, retries_5s_limit=5, is_new=False
        )
        result = self._run_test_with_duration(mock_test, current_time_ns)
        assert result is False

    def test_duration_under_5s_within_limit(self):
        """Test with duration ≤5s and retry count under limit should return True."""
        mock_test, current_time_ns = self._create_mock_test(duration_s=3.0, num_retries=0, retries_5s_limit=5)
        result = self._run_test_with_duration(mock_test, current_time_ns)
        assert result is True

    def test_duration_under_5s_at_limit(self):
        """Test with duration ≤5s and retry count at limit should return False."""
        mock_test, current_time_ns = self._create_mock_test(duration_s=4.0, num_retries=5, retries_5s_limit=5)
        result = self._run_test_with_duration(mock_test, current_time_ns)
        assert result is False

    def test_duration_under_10s_within_limit(self):
        """Test with duration ≤10s and retry count under limit should return True."""
        mock_test, current_time_ns = self._create_mock_test(duration_s=8.0, num_retries=1, retries_10s_limit=3)
        result = self._run_test_with_duration(mock_test, current_time_ns)
        assert result is True

    def test_duration_under_30s_within_limit(self):
        """Test with duration ≤30s and retry count under limit should return True."""
        mock_test, current_time_ns = self._create_mock_test(duration_s=25.0, num_retries=0, retries_30s_limit=2)
        result = self._run_test_with_duration(mock_test, current_time_ns)
        assert result is True

    def test_duration_under_5m_within_limit(self):
        """Test with duration ≤5m (300s) and retry count under limit should return True."""
        mock_test, current_time_ns = self._create_mock_test(duration_s=120.0, num_retries=0, retries_5m_limit=1)
        result = self._run_test_with_duration(mock_test, current_time_ns)
        assert result is True

    def test_duration_over_5m_returns_false(self):
        """Test with duration >5m (300s) should return False (no retries for very slow tests)."""
        mock_test, current_time_ns = self._create_mock_test(duration_s=400.0, num_retries=0, retries_5m_limit=5)
        result = self._run_test_with_duration(mock_test, current_time_ns)
        assert result is False

    def test_duration_exactly_5s_uses_5s_bucket(self):
        """Test with duration exactly 5s should use the 5s bucket."""
        mock_test, current_time_ns = self._create_mock_test(duration_s=5.0, num_retries=2, retries_5s_limit=3)
        result = self._run_test_with_duration(mock_test, current_time_ns)
        assert result is True

    def test_duration_exactly_10s_uses_10s_bucket(self):
        """Test with duration exactly 10s should use the 10s bucket."""
        mock_test, current_time_ns = self._create_mock_test(
            duration_s=10.0,
            num_retries=2,
            retries_5s_limit=1,  # Would fail in 5s bucket
            retries_10s_limit=3,  # But passes in 10s bucket
        )
        result = self._run_test_with_duration(mock_test, current_time_ns)
        assert result is True

    def test_duration_exactly_300s_uses_5m_bucket(self):
        """Test with duration exactly 300s should use the 5m bucket."""
        mock_test, current_time_ns = self._create_mock_test(duration_s=300.0, num_retries=0, retries_5m_limit=1)
        result = self._run_test_with_duration(mock_test, current_time_ns)
        assert result is True

    def test_span_without_start_ns_returns_true(self):
        """If span has no start_ns, should conservatively return True."""
        mock_test, _ = self._create_mock_test(duration_s=0, num_retries=0, retries_5s_limit=5)
        mock_test._span.start_ns = None
        result = mock_test.efd_should_retry(require_finished=False)
        assert result is True

    def test_faulty_session_returns_false(self):
        """If session is faulty, should return False."""
        mock_test, current_time_ns = self._create_mock_test(duration_s=3.0, num_retries=0, retries_5s_limit=5)
        mock_test.get_session.return_value.efd_is_faulty_session.return_value = True
        result = self._run_test_with_duration(mock_test, current_time_ns)
        assert result is False

    def test_abort_reason_set_returns_false(self):
        """If abort reason is set, should return False."""
        mock_test, current_time_ns = self._create_mock_test(duration_s=3.0, num_retries=0, retries_5s_limit=5)
        mock_test._efd_abort_reason = "Some reason"
        result = self._run_test_with_duration(mock_test, current_time_ns)
        assert result is False

    def test_zero_duration_uses_5s_bucket(self):
        """Test with zero duration should use the 5s bucket."""
        mock_test, current_time_ns = self._create_mock_test(duration_s=0.0, num_retries=0, retries_5s_limit=5)
        result = self._run_test_with_duration(mock_test, current_time_ns)
        assert result is True

    def test_very_small_duration_uses_5s_bucket(self):
        """Test with very small duration (milliseconds) should use the 5s bucket."""
        mock_test, current_time_ns = self._create_mock_test(duration_s=0.001, num_retries=0, retries_5s_limit=5)
        result = self._run_test_with_duration(mock_test, current_time_ns)
        assert result is True

    def test_all_retry_limits_zero_returns_false(self):
        """Test when all retry limits are 0, should return False."""
        mock_test, current_time_ns = self._create_mock_test(
            duration_s=3.0,
            num_retries=0,
            retries_5s_limit=0,
            retries_10s_limit=0,
            retries_30s_limit=0,
            retries_5m_limit=0,
        )
        result = self._run_test_with_duration(mock_test, current_time_ns)
        assert result is False

    def test_boundary_5s_to_10s(self):
        """Test boundary between 5s and 10s buckets (5.1s should use 10s bucket)."""
        mock_test, current_time_ns = self._create_mock_test(
            duration_s=5.1,
            num_retries=0,
            retries_5s_limit=0,  # 5s bucket exhausted
            retries_10s_limit=5,  # But 10s bucket available
        )
        result = self._run_test_with_duration(mock_test, current_time_ns)
        assert result is True

    def test_boundary_10s_to_30s(self):
        """Test boundary between 10s and 30s buckets."""
        mock_test, current_time_ns = self._create_mock_test(
            duration_s=10.1,
            num_retries=0,
            retries_10s_limit=0,  # 10s bucket exhausted
            retries_30s_limit=3,  # But 30s bucket available
        )
        result = self._run_test_with_duration(mock_test, current_time_ns)
        assert result is True

    def test_boundary_30s_to_5m(self):
        """Test boundary between 30s and 5m buckets."""
        mock_test, current_time_ns = self._create_mock_test(
            duration_s=30.1,
            num_retries=0,
            retries_30s_limit=0,  # 30s bucket exhausted
            retries_5m_limit=2,  # But 5m bucket available
        )
        result = self._run_test_with_duration(mock_test, current_time_ns)
        assert result is True

    def _create_mock_test(
        self,
        duration_s,
        num_retries,
        retries_5s_limit=10,
        retries_10s_limit=5,
        retries_30s_limit=3,
        retries_5m_limit=2,
        efd_enabled=True,
        is_new=True,
    ):
        """Helper to create a mock test object with specific duration and retry settings."""

        mock_test = mock.Mock(spec=TestVisibilityTest)

        # Mock span with start_ns
        mock_test._span = mock.Mock()
        # Set start_ns to a fixed value
        start_ns = 1000000000000  # Some nanosecond timestamp
        mock_test._span.start_ns = start_ns

        # Mock EFD retries
        mock_test._efd_retries = [mock.Mock()] * num_retries

        # Mock EFD settings
        mock_test._session_settings = mock.Mock()
        mock_test._session_settings.efd_settings = mock.Mock()
        mock_test._session_settings.efd_settings.enabled = efd_enabled
        mock_test._session_settings.efd_settings.slow_test_retries_5s = retries_5s_limit
        mock_test._session_settings.efd_settings.slow_test_retries_10s = retries_10s_limit
        mock_test._session_settings.efd_settings.slow_test_retries_30s = retries_30s_limit
        mock_test._session_settings.efd_settings.slow_test_retries_5m = retries_5m_limit

        # Mock session
        mock_test.get_session = mock.Mock()
        mock_test.get_session.return_value.efd_is_faulty_session.return_value = False

        # Mock abort reason
        mock_test._efd_abort_reason = None

        # Mock is_new
        mock_test.is_new = mock.Mock(return_value=is_new)

        # Bind the real method to the mock
        mock_test.efd_should_retry = TestVisibilityTest.efd_should_retry.__get__(mock_test, TestVisibilityTest)

        # Calculate what time_ns() should return based on desired duration
        current_time_ns = start_ns + int(duration_s * 1e9)

        return mock_test, current_time_ns

    def _run_test_with_duration(self, mock_test, current_time_ns):
        """Helper to run the test with mocked time."""
        with mock.patch("ddtrace.internal.utils.time.Time.time_ns", return_value=current_time_ns):
            return mock_test.efd_should_retry(require_finished=False)
