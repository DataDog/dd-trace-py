"""Integration tests for coverage report upload functionality."""

import gzip
import json
from unittest.mock import Mock
from unittest.mock import patch

from _pytest.pytester import Pytester
from pytest import MonkeyPatch

from ddtrace.testing.internal.http import BackendResult
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

        # Mock the coverage connector to capture the upload
        mock_coverage_connector = Mock()
        uploaded_files = []

        def capture_post_files(endpoint, files, **kwargs):
            uploaded_files.append({"endpoint": endpoint, "files": files})
            return BackendResult(response=Mock(status=200), response_length=100)

        mock_coverage_connector.post_files.side_effect = capture_post_files

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
            monkeypatch.context() as m,
        ):
            m.setenv("DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED", "1")

            # Patch the coverage connector
            with patch(
                "ddtrace.testing.internal.api_client.BackendConnectorSetup.get_connector_for_subdomain",
                return_value=mock_coverage_connector,
            ):
                with EventCapture.capture() as event_capture:
                    result = pytester.inline_run("--ddtrace", "--cov", "-v", "-s")

        # Verify test passed
        assert result.ret == 0

        # Verify coverage report was uploaded
        assert len(uploaded_files) == 1
        upload = uploaded_files[0]

        # Verify endpoint
        assert upload["endpoint"] == "/api/v2/cicovreprt"

        # Verify files structure
        files = upload["files"]
        assert len(files) == 2

        # Verify coverage file
        coverage_file = files[0]
        assert coverage_file.name == "coverage"
        assert coverage_file.filename == "coverage.lcov.gz"
        assert coverage_file.content_type == "application/gzip"

        # Decompress and verify LCOV content
        lcov_content = gzip.decompress(coverage_file.data).decode("utf-8")
        assert "SF:" in lcov_content  # Source file
        assert "DA:" in lcov_content  # Line data
        assert "end_of_record" in lcov_content

        # Verify event file
        event_file = files[1]
        assert event_file.name == "event"
        assert event_file.filename == "event.json"
        assert event_file.content_type == "application/json"

        # Parse and verify event JSON
        event_data = json.loads(event_file.data.decode("utf-8"))
        assert event_data["type"] == "coverage_report"
        assert event_data["format"] == "lcov"
        assert "timestamp" in event_data

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

        mock_coverage_connector = Mock()

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
            monkeypatch.context() as m,
        ):
            # Explicitly disable coverage report upload
            m.setenv("DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED", "0")

            with patch(
                "ddtrace.testing.internal.api_client.BackendConnectorSetup.get_connector_for_subdomain",
                return_value=mock_coverage_connector,
            ):
                result = pytester.inline_run("--ddtrace", "--cov", "-v", "-s")

        # Verify test passed
        assert result.ret == 0

        # Verify NO coverage report was uploaded
        assert mock_coverage_connector.post_files.call_count == 0

    def test_coverage_report_upload_without_pytest_cov(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test coverage report upload starts its own coverage when pytest-cov is not enabled."""
        pytester.makepyfile(
            test_sample="""
            def multiply(a, b):
                return a * b

            def test_multiply():
                assert multiply(2, 3) == 6
            """
        )

        mock_coverage_connector = Mock()
        uploaded_files = []

        def capture_post_files(endpoint, files, **kwargs):
            uploaded_files.append({"endpoint": endpoint, "files": files})
            return BackendResult(response=Mock(status=200))

        mock_coverage_connector.post_files.side_effect = capture_post_files

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
            monkeypatch.context() as m,
        ):
            m.setenv("DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED", "1")

            with patch(
                "ddtrace.testing.internal.api_client.BackendConnectorSetup.get_connector_for_subdomain",
                return_value=mock_coverage_connector,
            ):
                # Run WITHOUT --cov flag
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        # Verify test passed
        assert result.ret == 0

        # Verify coverage report was uploaded (started by plugin)
        assert len(uploaded_files) == 1

        # Verify the LCOV report contains data
        coverage_file = uploaded_files[0]["files"][0]
        lcov_content = gzip.decompress(coverage_file.data).decode("utf-8")
        assert "SF:" in lcov_content

    def test_coverage_report_includes_git_tags(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test that coverage report event includes git and CI tags."""
        pytester.makepyfile(
            test_sample="""
            def test_ok():
                assert True
            """
        )

        mock_coverage_connector = Mock()
        uploaded_event = []

        def capture_post_files(endpoint, files, **kwargs):
            # Extract the event file
            event_file = next(f for f in files if f.name == "event")
            uploaded_event.append(json.loads(event_file.data.decode("utf-8")))
            return BackendResult(response=Mock(status=200))

        mock_coverage_connector.post_files.side_effect = capture_post_files

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
            monkeypatch.context() as m,
        ):
            m.setenv("DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED", "1")
            # Set some git/CI environment variables
            m.setenv("DD_GIT_REPOSITORY_URL", "https://github.com/test/repo.git")
            m.setenv("DD_GIT_COMMIT_SHA", "abc123")
            m.setenv("DD_GIT_BRANCH", "main")

            with patch(
                "ddtrace.testing.internal.api_client.BackendConnectorSetup.get_connector_for_subdomain",
                return_value=mock_coverage_connector,
            ):
                pytester.inline_run("--ddtrace", "--cov", "-v", "-s")

        # Verify git tags are in the event
        assert len(uploaded_event) == 1
        event_data = uploaded_event[0]

        assert "git.repository_url" in event_data
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

        mock_coverage_connector = Mock()
        # Simulate upload failure
        mock_coverage_connector.post_files.return_value = BackendResult(
            error_type="NETWORK", error_description="Connection timeout"
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
            monkeypatch.context() as m,
        ):
            m.setenv("DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED", "1")

            with patch(
                "ddtrace.testing.internal.api_client.BackendConnectorSetup.get_connector_for_subdomain",
                return_value=mock_coverage_connector,
            ):
                result = pytester.inline_run("--ddtrace", "--cov", "-v", "-s")

        # Verify test still passed despite upload failure
        assert result.ret == 0

        # Verify upload was attempted
        assert mock_coverage_connector.post_files.call_count == 1


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

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
            monkeypatch.context() as m,
        ):
            m.setenv("DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED", "1")

            with EventCapture.capture() as event_capture:
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

        mock_coverage_connector = Mock()
        uploaded_lcov = []

        def capture_post_files(endpoint, files, **kwargs):
            coverage_file = next(f for f in files if f.name == "coverage")
            lcov_content = gzip.decompress(coverage_file.data).decode("utf-8")
            uploaded_lcov.append(lcov_content)
            return BackendResult(response=Mock(status=200))

        mock_coverage_connector.post_files.side_effect = capture_post_files

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
            monkeypatch.context() as m,
        ):
            m.setenv("DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED", "1")

            with patch(
                "ddtrace.testing.internal.api_client.BackendConnectorSetup.get_connector_for_subdomain",
                return_value=mock_coverage_connector,
            ):
                pytester.inline_run("--ddtrace", "--cov", "-v", "-s")

        assert len(uploaded_lcov) == 1
        lcov = uploaded_lcov[0]

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
