"""Integration tests for coverage report upload functionality in pytest plugin V2."""

import os
from unittest.mock import Mock
from unittest.mock import patch

from ddtrace.internal.ci_visibility.recorder import CIVisibility
from ddtrace.internal.ci_visibility.writer import CIVisibilityWriter


class TestPytestV2CoverageUpload:
    """Integration tests for pytest plugin V2 coverage report upload."""

    def test_is_coverage_report_upload_enabled_api_settings(self):
        """Test _is_coverage_report_upload_enabled reads from API settings."""
        from ddtrace.contrib.internal.pytest._plugin_v2 import _is_coverage_report_upload_enabled

        # Test enabled
        mock_service = Mock()
        mock_api_settings = Mock()
        mock_api_settings.coverage_report_upload_enabled = True
        mock_service._api_settings = mock_api_settings

        with patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.require_ci_visibility_service",
            return_value=mock_service,
        ):
            assert _is_coverage_report_upload_enabled() is True

        # Test disabled
        mock_api_settings.coverage_report_upload_enabled = False
        with patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.require_ci_visibility_service",
            return_value=mock_service,
        ):
            assert _is_coverage_report_upload_enabled() is False

    def test_is_coverage_report_upload_enabled_env_override(self):
        """Test environment variable overrides API settings."""
        from ddtrace.contrib.internal.pytest._plugin_v2 import _is_coverage_report_upload_enabled

        # API settings disabled
        mock_service = Mock()
        mock_api_settings = Mock()
        mock_api_settings.coverage_report_upload_enabled = False
        mock_service._api_settings = mock_api_settings

        with patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.require_ci_visibility_service",
            return_value=mock_service,
        ):
            # Test env var enabled (should override)
            with patch.dict(os.environ, {"DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED": "1"}):
                assert _is_coverage_report_upload_enabled() is True

            # Test env var disabled (should respect API setting)
            with patch.dict(os.environ, {"DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED": "0"}):
                assert _is_coverage_report_upload_enabled() is False

    def test_is_coverage_report_upload_enabled_exception_handling(self):
        """Test graceful handling when CI visibility service is not available."""
        from ddtrace.contrib.internal.pytest._plugin_v2 import _is_coverage_report_upload_enabled

        with patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.require_ci_visibility_service",
            side_effect=Exception("Service not available"),
        ):
            # Should not raise, should return False
            assert _is_coverage_report_upload_enabled() is False

            # But env var should still work
            with patch.dict(os.environ, {"DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED": "1"}):
                assert _is_coverage_report_upload_enabled() is True

    @patch("ddtrace.contrib.internal.pytest._plugin_v2.log")
    def test_is_coverage_report_upload_enabled_logs_debug(self, mock_log):
        """Test that exceptions are logged at debug level."""
        from ddtrace.contrib.internal.pytest._plugin_v2 import _is_coverage_report_upload_enabled

        with patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.require_ci_visibility_service",
            side_effect=RuntimeError("Service error"),
        ):
            _is_coverage_report_upload_enabled()

            mock_log.debug.assert_called_with(
                "Unable to check if coverage report upload is enabled from settings", exc_info=True
            )

    def test_pytest_sessionstart_coverage_decision(self):
        """Test that coverage is started in sessionstart when conditions are met."""
        from ddtrace.contrib.internal.pytest._plugin_v2 import pytest_sessionstart

        mock_session = Mock()
        mock_session.config = Mock()

        # Mock InternalTestSession for workspace path
        mock_test_session = Mock()
        mock_test_session.get_workspace_path.return_value = "/test/workspace"

        with (
            patch("ddtrace.contrib.internal.pytest._plugin_v2.is_test_visibility_enabled", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_pytest_cov_enabled", return_value=False),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_coverage_available", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_coverage_report_upload_enabled", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2.InternalTestSession", mock_test_session),
            patch("ddtrace.contrib.internal.pytest._plugin_v2.start_coverage") as mock_start_coverage,
            patch("ddtrace.contrib.internal.pytest._plugin_v2.log") as mock_log,
        ):
            pytest_sessionstart(mock_session)

            # Coverage should be started
            mock_start_coverage.assert_called_once_with(source=["/test/workspace"])
            mock_log.debug.assert_called_with("Started coverage.py for report upload")

    def test_pytest_sessionstart_coverage_not_started_when_disabled(self):
        """Test coverage is not started when upload is disabled."""
        from ddtrace.contrib.internal.pytest._plugin_v2 import pytest_sessionstart

        mock_session = Mock()
        mock_session.config = Mock()

        with (
            patch("ddtrace.contrib.internal.pytest._plugin_v2.is_test_visibility_enabled", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_pytest_cov_enabled", return_value=False),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_coverage_available", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_coverage_report_upload_enabled", return_value=False),
            patch("ddtrace.contrib.internal.pytest._plugin_v2.start_coverage") as mock_start_coverage,
        ):
            pytest_sessionstart(mock_session)

            # Coverage should not be started
            mock_start_coverage.assert_not_called()

    def test_pytest_sessionfinish_calls_handle_coverage_report(self):
        """Test that pytest_sessionfinish calls handle_coverage_report when appropriate."""
        from ddtrace.contrib.internal.pytest._plugin_v2 import _pytest_sessionfinish

        mock_session = Mock()
        mock_session.config = Mock()

        # Set up mocks to enable coverage upload path
        with (
            patch("ddtrace.contrib.internal.pytest._plugin_v2.is_test_visibility_enabled", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_coverage_patched", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_pytest_cov_enabled", return_value=True),
            patch(
                "ddtrace.contrib.internal.pytest._plugin_v2._is_coverage_invoked_by_coverage_run", return_value=False
            ),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_coverage_report_upload_enabled", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2.handle_coverage_report") as mock_handle_coverage,
            patch("ddtrace.contrib.internal.pytest._plugin_v2.get_coverage_percentage", return_value=85.0),
        ):
            _pytest_sessionfinish(mock_session, 0)

            # handle_coverage_report should be called
            mock_handle_coverage.assert_called_once()

            # Verify the call parameters
            call_args = mock_handle_coverage.call_args
            assert call_args[0][0] == mock_session  # session
            # The upload function should be callable
            assert callable(call_args[0][1])  # upload_func
            assert callable(call_args[0][2])  # is_pytest_cov_enabled_func

    def test_pytest_sessionfinish_no_coverage_upload_when_disabled(self):
        """Test handle_coverage_report is not called when upload disabled."""
        from ddtrace.contrib.internal.pytest._plugin_v2 import _pytest_sessionfinish

        mock_session = Mock()
        mock_session.config = Mock()

        with (
            patch("ddtrace.contrib.internal.pytest._plugin_v2.is_test_visibility_enabled", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_coverage_patched", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_pytest_cov_enabled", return_value=True),
            patch(
                "ddtrace.contrib.internal.pytest._plugin_v2._is_coverage_invoked_by_coverage_run", return_value=False
            ),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_coverage_report_upload_enabled", return_value=False),
            patch("ddtrace.contrib.internal.pytest._plugin_v2.handle_coverage_report") as mock_handle_coverage,
            patch("ddtrace.contrib.internal.pytest._plugin_v2.get_coverage_percentage", return_value=85.0),
        ):
            _pytest_sessionfinish(mock_session, 0)

            # handle_coverage_report should not be called
            mock_handle_coverage.assert_not_called()

    def test_coverage_upload_function_integration(self):
        """Test that the upload function passed to handle_coverage_report works correctly."""
        from ddtrace.contrib.internal.pytest._plugin_v2 import _pytest_sessionfinish

        mock_session = Mock()
        mock_session.config = Mock()

        # Mock CI visibility recorder
        mock_recorder = Mock(spec=CIVisibility)
        mock_recorder.upload_coverage_report.return_value = True

        captured_upload_func = None

        def capture_upload_func(session, upload_func, *args):
            nonlocal captured_upload_func
            captured_upload_func = upload_func

        with (
            patch("ddtrace.contrib.internal.pytest._plugin_v2.is_test_visibility_enabled", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_coverage_patched", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_pytest_cov_enabled", return_value=True),
            patch(
                "ddtrace.contrib.internal.pytest._plugin_v2._is_coverage_invoked_by_coverage_run", return_value=False
            ),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_coverage_report_upload_enabled", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2.handle_coverage_report", side_effect=capture_upload_func),
            patch("ddtrace.contrib.internal.pytest._plugin_v2.get_coverage_percentage", return_value=85.0),
            patch("ddtrace.ext.test_visibility.api.require_ci_visibility_service", return_value=mock_recorder),
        ):
            _pytest_sessionfinish(mock_session, 0)

        # Test the captured upload function
        assert captured_upload_func is not None

        test_coverage_data = b"SF:test.py\nDA:1,1\nend_of_record"
        result = captured_upload_func(test_coverage_data, "lcov")

        assert result is True
        mock_recorder.upload_coverage_report.assert_called_once_with(test_coverage_data, "lcov")

    def test_coverage_upload_function_handles_exceptions(self):
        """Test that upload function handles exceptions gracefully."""
        from ddtrace.contrib.internal.pytest._plugin_v2 import _pytest_sessionfinish

        mock_session = Mock()
        mock_session.config = Mock()

        # Mock CI visibility recorder that raises exception
        mock_recorder = Mock(spec=CIVisibility)
        mock_recorder.upload_coverage_report.side_effect = RuntimeError("Upload failed")

        captured_upload_func = None

        def capture_upload_func(session, upload_func, *args):
            nonlocal captured_upload_func
            captured_upload_func = upload_func

        with (
            patch("ddtrace.contrib.internal.pytest._plugin_v2.is_test_visibility_enabled", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_coverage_patched", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_pytest_cov_enabled", return_value=True),
            patch(
                "ddtrace.contrib.internal.pytest._plugin_v2._is_coverage_invoked_by_coverage_run", return_value=False
            ),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_coverage_report_upload_enabled", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2.handle_coverage_report", side_effect=capture_upload_func),
            patch("ddtrace.contrib.internal.pytest._plugin_v2.get_coverage_percentage", return_value=85.0),
            patch("ddtrace.ext.test_visibility.api.require_ci_visibility_service", return_value=mock_recorder),
        ):
            _pytest_sessionfinish(mock_session, 0)

        # Test that upload function handles exception gracefully
        test_coverage_data = b"SF:test.py\nDA:1,1\nend_of_record"
        result = captured_upload_func(test_coverage_data, "lcov")

        assert result is False  # Should return False on exception

    def test_coverage_upload_with_native_collection(self):
        """Test coverage upload when using native collection (no pytest-cov)."""
        from ddtrace.contrib.internal.pytest._plugin_v2 import _pytest_sessionfinish

        mock_session = Mock()
        mock_session.config = Mock()

        with (
            patch("ddtrace.contrib.internal.pytest._plugin_v2.is_test_visibility_enabled", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_coverage_patched", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_pytest_cov_enabled", return_value=False),
            patch(
                "ddtrace.contrib.internal.pytest._plugin_v2._is_coverage_invoked_by_coverage_run", return_value=False
            ),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_coverage_report_upload_enabled", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2.handle_coverage_report") as mock_handle_coverage,
            patch("ddtrace.contrib.internal.pytest._plugin_v2.get_coverage_percentage", return_value=90.0),
        ):
            _pytest_sessionfinish(mock_session, 0)

            # Should still call handle_coverage_report for native collection
            mock_handle_coverage.assert_called_once()

    def test_coverage_upload_with_coverage_run(self):
        """Test coverage upload when invoked via coverage run."""
        from ddtrace.contrib.internal.pytest._plugin_v2 import _pytest_sessionfinish

        mock_session = Mock()
        mock_session.config = Mock()

        with (
            patch("ddtrace.contrib.internal.pytest._plugin_v2.is_test_visibility_enabled", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_coverage_patched", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_pytest_cov_enabled", return_value=False),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_coverage_invoked_by_coverage_run", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2._is_coverage_report_upload_enabled", return_value=True),
            patch("ddtrace.contrib.internal.pytest._plugin_v2.handle_coverage_report") as mock_handle_coverage,
            patch("ddtrace.contrib.internal.pytest._plugin_v2.run_coverage_report") as mock_run_coverage,
            patch("ddtrace.contrib.internal.pytest._plugin_v2.get_coverage_percentage", return_value=88.0),
        ):
            _pytest_sessionfinish(mock_session, 0)

            # Should call both run_coverage_report and handle_coverage_report
            mock_run_coverage.assert_called_once()
            mock_handle_coverage.assert_called_once()

    def test_writer_coverage_client_initialization(self):
        """Test that CIVisibilityWriter properly initializes coverage client."""
        writer = CIVisibilityWriter(
            intake_url="http://test.datadog.com",
            coverage_report_upload_enabled=True,
        )

        # Check that coverage report client was added
        coverage_clients = [c for c in writer._clients if c.__class__.__name__.endswith("CoverageReportClient")]
        assert len(coverage_clients) == 1

        coverage_client = coverage_clients[0]
        assert hasattr(coverage_client, "coverage_encoder")

        # Test the encoder property
        encoder = coverage_client.coverage_encoder
        assert hasattr(encoder, "encode_coverage_report")
        assert hasattr(encoder, "content_type")

    def test_writer_no_coverage_client_when_disabled(self):
        """Test CIVisibilityWriter doesn't create coverage client when disabled."""
        writer = CIVisibilityWriter(
            intake_url="http://test.datadog.com",
            coverage_report_upload_enabled=False,
        )

        # Check that no coverage report client was added
        coverage_clients = [c for c in writer._clients if c.__class__.__name__.endswith("CoverageReportClient")]
        assert len(coverage_clients) == 0
