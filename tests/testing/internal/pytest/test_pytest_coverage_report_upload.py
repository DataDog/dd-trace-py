"""Integration tests for coverage report upload functionality."""

import typing as t
from unittest.mock import Mock
from unittest.mock import patch

from _pytest.pytester import Pytester
from pytest import MonkeyPatch

from tests.testing.mocks import CoverageReportUploadCapture
from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


class TestPytestCoverageReportUpload:
    """Tests for coverage report upload integration with pytest plugin."""

    def test_coverage_report_upload_enabled_with_pytest_cov(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test coverage report is uploaded when enabled with pytest-cov."""
        pytester.makepyfile(
            test_sample="""
            def add(a, b):
                return a + b

            def test_add():
                assert add(1, 2) == 3
            """
        )

        with (
            EventCapture.capture() as event_capture,
            CoverageReportUploadCapture.capture() as upload_capture,
        ):
            mock_client = mock_api_client_settings(
                coverage_report_upload_enabled=True, coverage_upload_capture=upload_capture
            )
            with (
                patch(
                    "ddtrace.testing.internal.session_manager.APIClient",
                    return_value=mock_client,
                ),
                setup_standard_mocks(),
                monkeypatch.context() as m,
            ):
                m.setenv("DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED", "1")

                result = pytester.inline_run("--ddtrace", "--cov", "-v", "-s")

        # Verify test passed
        assert result.ret == 0

        # Verify coverage report was uploaded
        coverage_uploads = upload_capture.get_coverage_report_uploads()
        assert len(coverage_uploads) == 1, f"Expected 1 coverage report upload, got {len(coverage_uploads)}"

        upload = coverage_uploads[0]

        # Verify files structure
        files = upload["files"]
        assert len(files) == 2

        # Verify coverage file
        coverage_file = next((f for f in files if f.name == "coverage"), None)
        assert coverage_file is not None
        assert coverage_file.filename == "coverage.lcov.gz"
        assert coverage_file.content_type == "application/gzip"

        # Decompress and verify LCOV content
        lcov_content = upload_capture.get_lcov_content(upload)
        assert "SF:" in lcov_content  # Source file
        assert "DA:" in lcov_content  # Line data
        assert "end_of_record" in lcov_content

        # Verify event file
        event_data = upload_capture.get_event_data(upload)
        assert event_data["type"] == "coverage_report"
        assert event_data["format"] == "lcov"

        # Verify session event includes coverage percentage
        [session_event] = event_capture.events_by_type("test_session_end")
        assert isinstance(session_event["content"]["metrics"].get("test.code_coverage.lines_pct"), float)

    def test_coverage_report_upload_disabled_by_default(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test coverage report is NOT uploaded when setting is disabled."""
        pytester.makepyfile(
            test_sample="""
            def test_ok():
                assert True
            """
        )

        with CoverageReportUploadCapture.capture() as upload_capture:
            with (
                patch(
                    "ddtrace.testing.internal.session_manager.APIClient",
                    return_value=mock_api_client_settings(coverage_report_upload_enabled=False),
                ),
                setup_standard_mocks(),
                monkeypatch.context() as m,
            ):
                # Explicitly disable coverage report upload
                m.setenv("DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED", "0")

                result = pytester.inline_run("--ddtrace", "--cov", "-v", "-s")

        # Verify test passed
        assert result.ret == 0

        # Verify NO coverage report was uploaded to cicovreprt endpoint
        coverage_uploads = upload_capture.get_coverage_report_uploads()
        assert len(coverage_uploads) == 0

    def test_coverage_report_upload_without_pytest_cov(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test coverage report upload uses ModuleCodeCollector when pytest-cov is not enabled."""
        pytester.makepyfile(
            test_sample="""
            def multiply(a, b):
                return a * b

            def test_multiply():
                assert multiply(2, 3) == 6
            """
        )

        with CoverageReportUploadCapture.capture() as upload_capture:
            mock_client = mock_api_client_settings(
                coverage_report_upload_enabled=True, coverage_upload_capture=upload_capture
            )
            with (
                patch(
                    "ddtrace.testing.internal.session_manager.APIClient",
                    return_value=mock_client,
                ),
                setup_standard_mocks(),
                monkeypatch.context() as m,
            ):
                m.setenv("DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED", "1")

                # Run WITHOUT --cov flag (will use ModuleCodeCollector)
                result = pytester.inline_run("--ddtrace", "-v", "-s")

            # Verify test passed
            assert result.ret == 0

            # Find coverage report uploads
            coverage_uploads = upload_capture.get_coverage_report_uploads()
            assert len(coverage_uploads) == 1, "Expected coverage report upload when using ModuleCodeCollector"

            # Verify the LCOV report contains data
            lcov_content = upload_capture.get_lcov_content(coverage_uploads[0])
            assert "SF:" in lcov_content

    def test_coverage_report_includes_git_tags(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test that coverage report event includes git and CI tags."""
        pytester.makepyfile(
            test_sample="""
            def test_ok():
                assert True
            """
        )

        # Mock env tags to include git data
        mock_env_tags = {
            "git.repository_url": "https://github.com/test/repo.git",
            "git.commit.sha": "abc123",
            "git.branch": "main",
        }

        with CoverageReportUploadCapture.capture() as upload_capture:
            mock_client = mock_api_client_settings(
                coverage_report_upload_enabled=True, coverage_upload_capture=upload_capture
            )
            with (
                patch(
                    "ddtrace.testing.internal.session_manager.APIClient",
                    return_value=mock_client,
                ),
                setup_standard_mocks(),
                patch("ddtrace.testing.internal.session_manager.get_env_tags", return_value=mock_env_tags),
                monkeypatch.context() as m,
            ):
                m.setenv("DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED", "1")

                pytester.inline_run("--ddtrace", "--cov", "-v", "-s")

        # Verify coverage report was uploaded
        coverage_uploads = upload_capture.get_coverage_report_uploads()
        assert len(coverage_uploads) == 1

        # Extract and verify event
        event_data = upload_capture.get_event_data(coverage_uploads[0])

        assert "git.repository_url" in event_data
        assert event_data["git.repository_url"] == "https://github.com/test/repo.git"
        assert "git.commit.sha" in event_data
        assert "git.branch" in event_data

    def test_coverage_report_upload_handles_errors_gracefully(
        self, pytester: Pytester, monkeypatch: MonkeyPatch
    ) -> None:
        """Test that coverage report upload errors don't fail the test run."""
        pytester.makepyfile(
            test_sample="""
            def test_ok():
                assert True
            """
        )

        upload_attempted = []

        def failing_upload_coverage_report(
            coverage_report_bytes: bytes, coverage_format: str, tags: t.Optional[t.Dict[str, str]] = None
        ):
            upload_attempted.append("/api/v2/cicovreprt")
            # Simulate upload failure by returning False
            return False

        # Create a custom mock API client that simulates upload failure
        mock_client = mock_api_client_settings(coverage_report_upload_enabled=True)
        mock_client.upload_coverage_report = Mock(side_effect=failing_upload_coverage_report)

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_client,
            ),
            setup_standard_mocks(),
            monkeypatch.context() as m,
        ):
            m.setenv("DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED", "1")

            result = pytester.inline_run("--ddtrace", "--cov", "-v", "-s")

        # Verify test still passed despite upload failure
        assert result.ret == 0

        # Verify upload was attempted
        coverage_uploads = [u for u in upload_attempted if u == "/api/v2/cicovreprt"]
        assert len(coverage_uploads) == 1


class TestCoverageReportGeneration:
    """Tests for coverage report generation."""

    def test_generate_lcov_report_returns_percentage(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test that generating LCOV report returns coverage percentage."""
        pytester.makepyfile(
            test_sample="""
            def covered():
                return True

            def uncovered():  # pragma: no cover
                return False

            def test_covered():
                assert covered()
            """
        )

        with EventCapture.capture() as event_capture:
            with (
                patch(
                    "ddtrace.testing.internal.session_manager.APIClient",
                    return_value=mock_api_client_settings(),
                ),
                setup_standard_mocks(),
                monkeypatch.context() as m,
            ):
                m.setenv("DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED", "1")

                pytester.inline_run("--ddtrace", "--cov", "-v", "-s")

        # Verify session event includes coverage percentage
        [session_event] = event_capture.events_by_type("test_session_end")
        coverage_pct = session_event["content"]["metrics"].get("test.code_coverage.lines_pct")

        assert coverage_pct is not None
        assert isinstance(coverage_pct, float)
        assert 0.0 <= coverage_pct <= 100.0

    def test_lcov_format_is_valid(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test that generated LCOV report has valid format."""
        pytester.makepyfile(
            test_sample="""
            def test_ok():
                x = 1
                y = 2
                assert x + y == 3
            """
        )

        with CoverageReportUploadCapture.capture() as upload_capture:
            mock_client = mock_api_client_settings(
                coverage_report_upload_enabled=True, coverage_upload_capture=upload_capture
            )
            with (
                patch(
                    "ddtrace.testing.internal.session_manager.APIClient",
                    return_value=mock_client,
                ),
                setup_standard_mocks(),
                monkeypatch.context() as m,
            ):
                m.setenv("DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED", "1")

                pytester.inline_run("--ddtrace", "--cov", "-v", "-s")

        # Verify coverage was uploaded
        coverage_uploads = upload_capture.get_coverage_report_uploads()
        assert len(coverage_uploads) == 1

        # Extract LCOV content
        lcov = upload_capture.get_lcov_content(coverage_uploads[0])

        # Verify LCOV format
        lines = lcov.strip().split("\n")

        # Should have SF (source file) entries
        assert any(line.startswith("SF:") for line in lines)

        # Should have DA (line data) entries
        assert any(line.startswith("DA:") for line in lines)

        # Should have LF (lines found) and LH (lines hit) entries
        assert any(line.startswith("LF:") for line in lines)
        assert any(line.startswith("LH:") for line in lines)

        # Should end with end_of_record
        assert "end_of_record" in lcov
