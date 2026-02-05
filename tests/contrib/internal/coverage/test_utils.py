"""Tests for shared coverage utilities."""

from unittest.mock import Mock
from unittest.mock import patch


class TestCoverageUtils:
    """Test suite for coverage utility functions."""

    def test_handle_coverage_report_successful_upload(self):
        """Test successful coverage report generation and upload."""
        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        mock_session = Mock()
        mock_upload_func = Mock(return_value=True)
        mock_is_pytest_cov_enabled = Mock(return_value=False)
        mock_stop_coverage = Mock()

        test_lcov_content = b"SF:test_file.py\nDA:1,1\nDA:2,1\nend_of_record\n"

        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_running", return_value=True),
            patch("ddtrace.contrib.internal.coverage.utils.generate_lcov_report", return_value=85.5) as mock_generate,
            patch("pathlib.Path.read_bytes", return_value=test_lcov_content),
            patch("pathlib.Path.unlink") as mock_unlink,
            patch("ddtrace.contrib.internal.coverage.utils.log") as mock_log,
        ):
            handle_coverage_report(mock_session, mock_upload_func, mock_is_pytest_cov_enabled, mock_stop_coverage)

            # Verify LCOV report was generated
            mock_generate.assert_called_once()

            # Verify upload was attempted
            mock_upload_func.assert_called_once_with(test_lcov_content, "lcov")

            # Verify cleanup occurred
            mock_unlink.assert_called_once()

            # Coverage should be stopped since pytest-cov is disabled
            mock_stop_coverage.assert_called_once_with(save=True)

            # Verify success log
            mock_log.info.assert_called_with("Successfully uploaded coverage report")

    def test_handle_coverage_report_upload_failure(self):
        """Test handling of upload failure."""
        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        mock_session = Mock()
        mock_upload_func = Mock(return_value=False)  # Upload fails
        mock_is_pytest_cov_enabled = Mock(return_value=False)
        mock_stop_coverage = Mock()

        test_lcov_content = b"SF:test_file.py\nDA:1,1\nend_of_record\n"

        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_running", return_value=True),
            patch("ddtrace.contrib.internal.coverage.utils.generate_lcov_report", return_value=75.0),
            patch("pathlib.Path.read_bytes", return_value=test_lcov_content),
            patch("pathlib.Path.unlink"),
            patch("ddtrace.contrib.internal.coverage.utils.log") as mock_log,
        ):
            handle_coverage_report(mock_session, mock_upload_func, mock_is_pytest_cov_enabled, mock_stop_coverage)

            # Upload should be attempted
            mock_upload_func.assert_called_once()

            # Should log warning on failure
            mock_log.warning.assert_called_with("Failed to upload coverage report")

            # Coverage should still be stopped
            mock_stop_coverage.assert_called_once_with(save=True)

    def test_handle_coverage_report_no_coverage_running(self):
        """Test when coverage is not running."""
        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        mock_session = Mock()
        mock_upload_func = Mock()
        mock_is_pytest_cov_enabled = Mock(return_value=False)
        mock_stop_coverage = Mock()

        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_running", return_value=False),
            patch("ddtrace.contrib.internal.coverage.utils.log") as mock_log,
        ):
            handle_coverage_report(mock_session, mock_upload_func, mock_is_pytest_cov_enabled, mock_stop_coverage)

            # Nothing should be called
            mock_upload_func.assert_not_called()
            mock_stop_coverage.assert_not_called()

            # Should log debug message
            mock_log.debug.assert_called_with("Coverage is not running, skipping coverage report upload")

    def test_handle_coverage_report_with_pytest_cov(self):
        """Test coverage upload when pytest-cov is enabled."""
        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        mock_session = Mock()
        mock_upload_func = Mock(return_value=True)
        mock_is_pytest_cov_enabled = Mock(return_value=True)  # pytest-cov enabled
        mock_stop_coverage = Mock()

        test_lcov_content = b"SF:test_file.py\nDA:1,1\nend_of_record\n"

        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_running", return_value=True),
            patch("ddtrace.contrib.internal.coverage.utils.generate_lcov_report", return_value=90.0),
            patch("pathlib.Path.read_bytes", return_value=test_lcov_content),
            patch("pathlib.Path.unlink"),
        ):
            handle_coverage_report(mock_session, mock_upload_func, mock_is_pytest_cov_enabled, mock_stop_coverage)

            # Upload should happen
            mock_upload_func.assert_called_once_with(test_lcov_content, "lcov")

            # Coverage should NOT be stopped (pytest-cov manages it)
            mock_stop_coverage.assert_not_called()

    def test_handle_coverage_report_pytest_cov_registration(self):
        """Test registration of pytest-cov coverage instance."""
        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        mock_session = Mock()
        mock_upload_func = Mock(return_value=True)
        mock_is_pytest_cov_enabled = Mock(return_value=True)

        # Mock pytest-cov plugin
        mock_cov_controller = Mock()
        mock_cov_instance = Mock()
        mock_cov_controller.cov = mock_cov_instance

        mock_plugin = Mock()
        mock_plugin.cov_controller = mock_cov_controller

        # Mock session.config.pluginmanager.list_name_plugin()
        mock_session.config.pluginmanager.list_name_plugin.return_value = [
            ("other_plugin", Mock()),
            ("pytest_cov_plugin", mock_plugin),
        ]

        test_lcov_content = b"SF:test_file.py\nend_of_record\n"

        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_running", side_effect=[False, True]),
            patch("ddtrace.contrib.internal.coverage.utils.set_coverage_instance") as mock_set_coverage,
            patch("ddtrace.contrib.internal.coverage.utils.generate_lcov_report", return_value=85.0),
            patch("pathlib.Path.read_bytes", return_value=test_lcov_content),
            patch("pathlib.Path.unlink"),
            patch("ddtrace.contrib.internal.coverage.utils.log") as mock_log,
        ):
            handle_coverage_report(
                mock_session,
                mock_upload_func,
                mock_is_pytest_cov_enabled,
            )

            # Should register pytest-cov coverage instance
            mock_set_coverage.assert_called_once_with(mock_cov_instance)
            mock_log.debug.assert_any_call("Registered pytest-cov coverage instance with ddtrace")

            # Upload should happen after registration
            mock_upload_func.assert_called_once()

    def test_handle_coverage_report_exception_during_generation(self):
        """Test exception handling during coverage report generation."""
        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        mock_session = Mock()
        mock_upload_func = Mock()
        mock_is_pytest_cov_enabled = Mock(return_value=False)
        mock_stop_coverage = Mock()

        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_running", return_value=True),
            patch(
                "ddtrace.contrib.internal.coverage.utils.generate_lcov_report",
                side_effect=RuntimeError("Coverage generation failed"),
            ),
            patch("ddtrace.contrib.internal.coverage.utils.log") as mock_log,
        ):
            # Should not raise exception
            handle_coverage_report(mock_session, mock_upload_func, mock_is_pytest_cov_enabled, mock_stop_coverage)

            # Upload should not be called
            mock_upload_func.assert_not_called()

            # Should log exception
            mock_log.exception.assert_called()

            # Should still try to stop coverage
            mock_stop_coverage.assert_called_once_with(save=True)

    def test_handle_coverage_report_exception_during_upload(self):
        """Test exception handling during upload."""
        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        mock_session = Mock()
        mock_upload_func = Mock(side_effect=RuntimeError("Upload failed"))
        mock_is_pytest_cov_enabled = Mock(return_value=False)
        mock_stop_coverage = Mock()

        test_lcov_content = b"SF:test_file.py\nend_of_record\n"

        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_running", return_value=True),
            patch("ddtrace.contrib.internal.coverage.utils.generate_lcov_report", return_value=80.0),
            patch("pathlib.Path.read_bytes", return_value=test_lcov_content),
            patch("pathlib.Path.unlink"),
            patch("ddtrace.contrib.internal.coverage.utils.log") as mock_log,
        ):
            # Should not raise exception
            handle_coverage_report(mock_session, mock_upload_func, mock_is_pytest_cov_enabled, mock_stop_coverage)

            # Should log exception
            mock_log.exception.assert_called()

            # Should still stop coverage
            mock_stop_coverage.assert_called_once_with(save=True)

    def test_handle_coverage_report_cleanup_failure(self):
        """Test graceful handling of cleanup failure."""
        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        mock_session = Mock()
        mock_upload_func = Mock(return_value=True)
        mock_is_pytest_cov_enabled = Mock(return_value=False)
        mock_stop_coverage = Mock()

        test_lcov_content = b"SF:test_file.py\nend_of_record\n"

        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_running", return_value=True),
            patch("ddtrace.contrib.internal.coverage.utils.generate_lcov_report", return_value=85.0),
            patch("pathlib.Path.read_bytes", return_value=test_lcov_content),
            patch("pathlib.Path.unlink", side_effect=OSError("Permission denied")),
            patch("ddtrace.contrib.internal.coverage.utils.log") as mock_log,
        ):
            # Should not raise exception
            handle_coverage_report(mock_session, mock_upload_func, mock_is_pytest_cov_enabled, mock_stop_coverage)

            # Upload should still happen
            mock_upload_func.assert_called_once()

            # Should log cleanup failure
            mock_log.debug.assert_any_call(
                "Failed to clean up temporary coverage report file: %s", mock_log.debug.call_args_list[-1][0][1]
            )

    def test_handle_coverage_report_no_stop_coverage_function(self):
        """Test when no stop_coverage function is provided."""
        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        mock_session = Mock()
        mock_upload_func = Mock(return_value=True)
        mock_is_pytest_cov_enabled = Mock(return_value=False)

        test_lcov_content = b"SF:test_file.py\nend_of_record\n"

        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_running", return_value=True),
            patch("ddtrace.contrib.internal.coverage.utils.generate_lcov_report", return_value=85.0),
            patch("pathlib.Path.read_bytes", return_value=test_lcov_content),
            patch("pathlib.Path.unlink"),
        ):
            # Call without stop_coverage function
            handle_coverage_report(
                mock_session,
                mock_upload_func,
                mock_is_pytest_cov_enabled,
            )

            # Should still work and upload
            mock_upload_func.assert_called_once()

    def test_handle_coverage_report_exception_in_stop_coverage(self):
        """Test graceful handling when stop_coverage fails."""
        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        mock_session = Mock()
        mock_upload_func = Mock(return_value=True)
        mock_is_pytest_cov_enabled = Mock(return_value=False)
        mock_stop_coverage = Mock(side_effect=RuntimeError("Stop failed"))

        test_lcov_content = b"SF:test_file.py\nend_of_record\n"

        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_running", return_value=True),
            patch("ddtrace.contrib.internal.coverage.utils.generate_lcov_report", return_value=85.0),
            patch("pathlib.Path.read_bytes", return_value=test_lcov_content),
            patch("pathlib.Path.unlink"),
            patch("ddtrace.contrib.internal.coverage.utils.log"),
        ):
            # Should not raise exception
            handle_coverage_report(mock_session, mock_upload_func, mock_is_pytest_cov_enabled, mock_stop_coverage)

            # Upload should still happen
            mock_upload_func.assert_called_once()

    def test_handle_coverage_report_top_level_exception(self):
        """Test top-level exception handling."""
        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        mock_session = Mock()
        mock_upload_func = Mock()
        mock_is_pytest_cov_enabled = Mock(side_effect=RuntimeError("Unexpected error"))

        with (
            patch("ddtrace.contrib.internal.coverage.utils.log") as mock_log,
        ):
            # Should not raise exception
            handle_coverage_report(
                mock_session,
                mock_upload_func,
                mock_is_pytest_cov_enabled,
            )

            # Should log top-level exception
            mock_log.exception.assert_called_with(
                "Error in coverage report upload handling: %s", mock_log.exception.call_args[0][1]
            )


class TestCoverageUtilityFunctions:
    """Test individual utility functions."""

    def test_is_coverage_loaded(self):
        """Test is_coverage_loaded function."""
        from ddtrace.contrib.internal.coverage.utils import is_coverage_loaded

        # Test when coverage is not loaded
        with patch.dict("sys.modules", {}, clear=False):
            if "coverage" in __import__("sys").modules:
                del __import__("sys").modules["coverage"]
            assert is_coverage_loaded() is False

        # Test when coverage is loaded
        with patch.dict("sys.modules", {"coverage": Mock()}, clear=False):
            assert is_coverage_loaded() is True

    def test_is_coverage_patched(self):
        """Test _is_coverage_patched function."""
        from ddtrace.contrib.internal.coverage.utils import _is_coverage_patched

        # Test when coverage is not loaded
        with patch("ddtrace.contrib.internal.coverage.utils.is_coverage_loaded", return_value=False):
            assert _is_coverage_patched() is False

        # Test when coverage is loaded but not patched
        mock_coverage = Mock()
        del mock_coverage._datadog_patch  # Ensure attribute doesn't exist
        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_loaded", return_value=True),
            patch.dict("sys.modules", {"coverage": mock_coverage}),
        ):
            assert _is_coverage_patched() is False

        # Test when coverage is loaded and patched
        mock_coverage._datadog_patch = True
        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_loaded", return_value=True),
            patch.dict("sys.modules", {"coverage": mock_coverage}),
        ):
            assert _is_coverage_patched() is True

    def test_command_invokes_coverage_run(self):
        """Test _command_invokes_coverage_run function."""
        from ddtrace.contrib.internal.coverage.utils import _command_invokes_coverage_run

        # Test positive cases
        assert _command_invokes_coverage_run(["coverage", "run", "-m", "pytest"]) is True
        assert _command_invokes_coverage_run(["python", "-m", "coverage", "run", "-m", "pytest"]) is True

        # Test negative cases
        assert _command_invokes_coverage_run(["pytest"]) is False
        assert _command_invokes_coverage_run(["python", "-m", "pytest"]) is False
        assert _command_invokes_coverage_run(["coverage", "report"]) is False

    def test_is_coverage_invoked_by_coverage_run(self):
        """Test _is_coverage_invoked_by_coverage_run function."""
        from ddtrace.contrib.internal.coverage.utils import _is_coverage_invoked_by_coverage_run

        # Test with environment variable
        with patch("ddtrace.contrib.internal.coverage.utils._get_config", return_value=True):
            assert _is_coverage_invoked_by_coverage_run() is True

        # Test with command line detection
        with (
            patch("ddtrace.contrib.internal.coverage.utils._get_config", return_value=False),
            patch("ddtrace.contrib.internal.coverage.utils._command_invokes_coverage_run", return_value=True),
        ):
            assert _is_coverage_invoked_by_coverage_run() is True

        # Test negative case
        with (
            patch("ddtrace.contrib.internal.coverage.utils._get_config", return_value=False),
            patch("ddtrace.contrib.internal.coverage.utils._command_invokes_coverage_run", return_value=False),
        ):
            assert _is_coverage_invoked_by_coverage_run() is False
