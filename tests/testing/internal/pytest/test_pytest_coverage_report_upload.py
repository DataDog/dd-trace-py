"""Integration tests for coverage report upload functionality."""

from contextlib import ExitStack
import typing as t
from unittest.mock import Mock
from unittest.mock import patch

from _pytest.pytester import Pytester
import pytest
from pytest import MonkeyPatch

from tests.testing.mocks import CoverageReportUploadCapture
from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


COVERAGE_UPLOAD_ENABLED_ENV = "DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED"


@pytest.fixture(autouse=True)
def isolate_coverage_upload_env(monkeypatch: MonkeyPatch) -> None:
    """Unset coverage upload env var so tests are not affected by external environment."""
    monkeypatch.delenv(COVERAGE_UPLOAD_ENABLED_ENV, raising=False)


def get_mock_git_env_tags():
    """Get mock git environment tags for CI testing."""
    return {
        "git.repository_url": "https://github.com/DataDog/dd-trace-py.git",
        "git.commit.sha": "test123abc",
        "git.branch": "test-branch",
        "git.commit.message": "Test commit",
        "git.commit.author.name": "Test Author",
        "git.commit.author.email": "test@example.com",
        "git.commit.committer.name": "Test Committer",
        "git.commit.committer.email": "test@example.com",
        "ci.provider.name": "github",
        "ci.pipeline.id": "test-pipeline-123",
        "ci.job.name": "test-job",
        "ci.workspace_path": "/tmp/test-workspace",
    }


def setup_git_mocks():
    """Set up comprehensive git mocking to prevent actual git commands."""
    mock_git_tags = get_mock_git_env_tags()

    # Mock the session manager's get_env_tags (this overrides setup_standard_mocks)
    mock_session_manager_env_tags = patch(
        "ddtrace.testing.internal.session_manager.get_env_tags", return_value=mock_git_tags
    )

    # Mock the main env_tags module function
    mock_env_tags_get_env_tags = patch("ddtrace.testing.internal.env_tags.get_env_tags", return_value=mock_git_tags)

    # Mock the git commands to prevent subprocess calls
    mock_git_tags_from_command = patch(
        "ddtrace.testing.internal.git.get_git_tags_from_git_command", return_value=mock_git_tags
    )

    mock_git_head_tags = patch("ddtrace.testing.internal.git.get_git_head_tags_from_git_command", return_value={})

    # Mock workspace path
    mock_workspace_path = patch("ddtrace.testing.internal.git.get_workspace_path", return_value="/tmp/test-workspace")

    return [
        mock_session_manager_env_tags,
        mock_env_tags_get_env_tags,
        mock_git_tags_from_command,
        mock_git_head_tags,
        mock_workspace_path,
    ]


def run_test_with_mocks(pytester, monkeypatch, mock_client, env_var_name, env_var_value, pytest_args):
    """Helper to run pytest tests with comprehensive mocking."""
    git_mocks = setup_git_mocks()

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_client,
            )
        )
        stack.enter_context(setup_standard_mocks())
        for mock in git_mocks:
            stack.enter_context(mock)
        m = stack.enter_context(monkeypatch.context())

        m.setenv(env_var_name, env_var_value)
        return pytester.inline_run(*pytest_args)


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

        with EventCapture.capture() as event_capture, CoverageReportUploadCapture.capture() as upload_capture:
            mock_client = mock_api_client_settings(
                coverage_report_upload_enabled=True, coverage_upload_capture=upload_capture
            )
            git_mocks = setup_git_mocks()

            with ExitStack() as stack:
                stack.enter_context(
                    patch(
                        "ddtrace.testing.internal.session_manager.APIClient",
                        return_value=mock_client,
                    )
                )
                stack.enter_context(setup_standard_mocks())
                for mock in git_mocks:
                    stack.enter_context(mock)
                m = stack.enter_context(monkeypatch.context())

                m.setenv(COVERAGE_UPLOAD_ENABLED_ENV, "1")

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
                m.setenv(COVERAGE_UPLOAD_ENABLED_ENV, "0")

                result = pytester.inline_run("--ddtrace", "--cov", "-v", "-s")

        # Verify test passed
        assert result.ret == 0

        # Verify NO coverage report was uploaded to cicovreprt endpoint
        coverage_uploads = upload_capture.get_coverage_report_uploads()
        assert len(coverage_uploads) == 0

    @pytest.mark.skip("Skipping due to flakiness: ")
    def test_coverage_report_upload_without_pytest_cov(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test coverage report upload uses coverage.py when pytest-cov is not enabled."""
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
            git_mocks = setup_git_mocks()

            with ExitStack() as stack:
                stack.enter_context(
                    patch(
                        "ddtrace.testing.internal.session_manager.APIClient",
                        return_value=mock_client,
                    )
                )
                stack.enter_context(setup_standard_mocks())
                for mock in git_mocks:
                    stack.enter_context(mock)

                # Fix flaky test: Mock get_workspace_path to return pytester's actual directory
                # This ensures coverage.py starts with the correct source path to the test files
                stack.enter_context(
                    patch(
                        "ddtrace.testing.internal.git.get_workspace_path",
                        return_value=str(pytester.path),
                    )
                )

                m = stack.enter_context(monkeypatch.context())

                m.setenv(COVERAGE_UPLOAD_ENABLED_ENV, "1")

                # Run WITHOUT --cov flag (will start coverage.py via start_coverage())
                result = pytester.inline_run("--ddtrace", "-v", "-s")

            # Verify test passed
            assert result.ret == 0

            # Find coverage report uploads
            coverage_uploads = upload_capture.get_coverage_report_uploads()
            assert len(coverage_uploads) == 1, (
                f"Expected coverage report upload when using coverage.py. Got {len(coverage_uploads)} uploads."
            )

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
                patch("tests.testing.mocks.get_env_tags", return_value=mock_env_tags),
                monkeypatch.context() as m,
            ):
                m.setenv(COVERAGE_UPLOAD_ENABLED_ENV, "1")

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
            coverage_report_bytes: bytes, coverage_format: str, tags: t.Optional[dict[str, str]] = None
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
            m.setenv(COVERAGE_UPLOAD_ENABLED_ENV, "1")

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
                m.setenv(COVERAGE_UPLOAD_ENABLED_ENV, "1")

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
            git_mocks = setup_git_mocks()

            with ExitStack() as stack:
                stack.enter_context(
                    patch(
                        "ddtrace.testing.internal.session_manager.APIClient",
                        return_value=mock_client,
                    )
                )
                stack.enter_context(setup_standard_mocks())
                for mock in git_mocks:
                    stack.enter_context(mock)
                m = stack.enter_context(monkeypatch.context())

                m.setenv(COVERAGE_UPLOAD_ENABLED_ENV, "1")

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


class TestCoverageReportUploadSettings:
    """Tests for backend settings vs environment variable precedence."""

    def test_backend_enabled_env_var_disabled(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test that backend setting overrides disabled env var."""
        pytester.makepyfile(
            test_sample="""
            def test_ok():
                assert True
            """
        )

        with CoverageReportUploadCapture.capture() as upload_capture:
            # Backend says enabled, env var says disabled
            mock_client = mock_api_client_settings(
                coverage_report_upload_enabled=True, coverage_upload_capture=upload_capture
            )

            result = run_test_with_mocks(
                pytester,
                monkeypatch,
                mock_client,
                COVERAGE_UPLOAD_ENABLED_ENV,
                "0",
                ["--ddtrace", "--cov", "-v", "-s"],
            )

        assert result.ret == 0

        # Backend setting should override env var - upload should occur
        coverage_uploads = upload_capture.get_coverage_report_uploads()
        assert len(coverage_uploads) == 1

    def test_backend_disabled_env_var_enabled(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test behavior when backend setting is disabled but env var is enabled."""
        pytester.makepyfile(
            test_sample="""
            def test_ok():
                assert True
            """
        )

        with CoverageReportUploadCapture.capture() as upload_capture:
            # Backend says disabled, env var says enabled
            mock_client = mock_api_client_settings(
                coverage_report_upload_enabled=False, coverage_upload_capture=upload_capture
            )

            result = run_test_with_mocks(
                pytester,
                monkeypatch,
                mock_client,
                COVERAGE_UPLOAD_ENABLED_ENV,
                "1",
                ["--ddtrace", "--cov", "-v", "-s"],
            )

        assert result.ret == 0

        # Backend setting should not take precedence here
        coverage_uploads = upload_capture.get_coverage_report_uploads()
        assert len(coverage_uploads) == 1  # Env var enabled overrides backend

    def test_both_backend_and_env_enabled(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test that upload works when both backend and env var are enabled."""
        pytester.makepyfile(
            test_sample="""
            def test_ok():
                assert True
            """
        )

        with CoverageReportUploadCapture.capture() as upload_capture:
            mock_client = mock_api_client_settings(
                coverage_report_upload_enabled=True, coverage_upload_capture=upload_capture
            )

            result = run_test_with_mocks(
                pytester,
                monkeypatch,
                mock_client,
                COVERAGE_UPLOAD_ENABLED_ENV,
                "1",
                ["--ddtrace", "--cov", "-v", "-s"],
            )

        assert result.ret == 0

        # Both enabled - upload should occur
        coverage_uploads = upload_capture.get_coverage_report_uploads()
        assert len(coverage_uploads) == 1

    def test_both_backend_and_env_disabled(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test that no upload occurs when both backend and env var are disabled."""
        pytester.makepyfile(
            test_sample="""
            def test_ok():
                assert True
            """
        )

        with CoverageReportUploadCapture.capture() as upload_capture:
            mock_client = mock_api_client_settings(
                coverage_report_upload_enabled=False, coverage_upload_capture=upload_capture
            )

            result = run_test_with_mocks(
                pytester,
                monkeypatch,
                mock_client,
                COVERAGE_UPLOAD_ENABLED_ENV,
                "0",
                ["--ddtrace", "--cov", "-v", "-s"],
            )

        assert result.ret == 0

        # Both disabled - no upload should occur
        coverage_uploads = upload_capture.get_coverage_report_uploads()
        assert len(coverage_uploads) == 0

    def test_env_var_invalid_values(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test handling of invalid environment variable values."""
        pytester.makepyfile(
            test_sample="""
            def test_ok():
                assert True
            """
        )

        test_cases = [
            ("invalid", "Backend enabled should still work with invalid env var"),
            ("", "Backend enabled should still work with empty env var"),
            ("yes", "Backend enabled should still work with non-numeric env var"),
            ("2", "Backend enabled should still work with non-boolean env var"),
        ]

        for env_value, description in test_cases:
            with CoverageReportUploadCapture.capture() as upload_capture:
                mock_client = mock_api_client_settings(
                    coverage_report_upload_enabled=True, coverage_upload_capture=upload_capture
                )

                result = run_test_with_mocks(
                    pytester,
                    monkeypatch,
                    mock_client,
                    COVERAGE_UPLOAD_ENABLED_ENV,
                    env_value,
                    ["--ddtrace", "--cov", "-v", "-s"],
                )

            assert result.ret == 0, description

            # Should still upload when backend is enabled regardless of invalid env var
            coverage_uploads = upload_capture.get_coverage_report_uploads()
            assert len(coverage_uploads) == 1, f"Failed for env_value='{env_value}': {description}"


class TestCoverageConfigurationEdgeCases:
    """Tests for configuration edge cases."""

    def test_pytest_cov_and_module_collector_both_active(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test behavior when both pytest-cov and ModuleCodeCollector might be active."""
        pytester.makepyfile(
            test_sample="""
            def add_numbers(a, b):
                return a + b

            def test_add():
                result = add_numbers(2, 3)
                assert result == 5
            """
        )

        with CoverageReportUploadCapture.capture() as upload_capture:
            mock_client = mock_api_client_settings(
                coverage_report_upload_enabled=True, coverage_upload_capture=upload_capture
            )

            result = run_test_with_mocks(
                pytester,
                monkeypatch,
                mock_client,
                COVERAGE_UPLOAD_ENABLED_ENV,
                "1",
                ["--ddtrace", "--cov", "-v", "-s"],
            )

        assert result.ret == 0

        # Should generate coverage report (pytest-cov takes precedence)
        coverage_uploads = upload_capture.get_coverage_report_uploads()
        assert len(coverage_uploads) == 1

        # Verify LCOV content is generated correctly
        lcov_content = upload_capture.get_lcov_content(coverage_uploads[0])
        assert "SF:" in lcov_content  # Source file entries
        assert "DA:" in lcov_content  # Line data entries

    def test_coverage_with_custom_config_file(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test coverage collection with custom coverage configuration."""
        # Create a custom .coveragerc file
        pytester.makefile(
            ".coveragerc",
            """
            [run]
            branch = True
            source = .
            omit =
                test_*
                */tests/*

            [report]
            precision = 2
            show_missing = True
            """,
        )

        pytester.makepyfile(
            test_sample="""
            def complex_function(x):
                if x > 0:
                    return x * 2
                else:
                    return x * -1

            def test_complex():
                assert complex_function(5) == 10
                assert complex_function(-3) == 3
            """
        )

        with CoverageReportUploadCapture.capture() as upload_capture:
            mock_client = mock_api_client_settings(
                coverage_report_upload_enabled=True, coverage_upload_capture=upload_capture
            )

            result = run_test_with_mocks(
                pytester,
                monkeypatch,
                mock_client,
                COVERAGE_UPLOAD_ENABLED_ENV,
                "1",
                ["--ddtrace", "--cov", "--cov-config=.coveragerc", "-v", "-s"],
            )

        assert result.ret == 0

        # Verify coverage report was generated with custom config
        coverage_uploads = upload_capture.get_coverage_report_uploads()
        assert len(coverage_uploads) == 1

        lcov_content = upload_capture.get_lcov_content(coverage_uploads[0])
        assert "SF:" in lcov_content  # Assert basic lcov structure is present

    def test_coverage_with_multiple_source_paths(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test coverage collection across multiple source directories."""
        # Create multiple source directories
        pytester.makepyfile(
            **{
                "src/module1.py": """
                def func1():
                    return "module1"
                """,
                "lib/module2.py": """
                def func2():
                    return "module2"
                """,
                "test_modules.py": """
                import sys
                sys.path.extend(['src', 'lib'])

                from module1 import func1
                from module2 import func2

                def test_both_modules():
                    assert func1() == "module1"
                    assert func2() == "module2"
                """,
            }
        )

        with CoverageReportUploadCapture.capture() as upload_capture:
            mock_client = mock_api_client_settings(
                coverage_report_upload_enabled=True, coverage_upload_capture=upload_capture
            )

            result = run_test_with_mocks(
                pytester,
                monkeypatch,
                mock_client,
                COVERAGE_UPLOAD_ENABLED_ENV,
                "1",
                ["--ddtrace", "--cov=src", "--cov=lib", "-v", "-s"],
            )

        assert result.ret == 0

        # Verify coverage includes both source directories
        coverage_uploads = upload_capture.get_coverage_report_uploads()
        assert len(coverage_uploads) == 1

        lcov_content = upload_capture.get_lcov_content(coverage_uploads[0])
        # Should contain entries for both modules
        assert "module1.py" in lcov_content
        assert "module2.py" in lcov_content

    def test_coverage_report_with_empty_test_suite(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test coverage report generation when no tests are collected."""
        # Create a file with no tests
        pytester.makepyfile(
            no_tests="""
            def unused_function():
                return "not covered"
            """
        )

        with CoverageReportUploadCapture.capture() as upload_capture:
            mock_client = mock_api_client_settings(
                coverage_report_upload_enabled=True, coverage_upload_capture=upload_capture
            )

            result = run_test_with_mocks(
                pytester,
                monkeypatch,
                mock_client,
                COVERAGE_UPLOAD_ENABLED_ENV,
                "1",
                ["--ddtrace", "--cov", "-v", "-s"],
            )

        # Should handle gracefully even with no tests
        assert result.ret == 5  # pytest exit code for no tests collected

        # May or may not upload coverage depending on implementation
        # Currently we send the report (it may be empty)
        coverage_uploads = upload_capture.get_coverage_report_uploads()
        assert len(coverage_uploads) == 1  # 0 would be acceptable too

    def test_coverage_report_format_validation_edge_cases(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test LCOV format validation with edge cases."""
        pytester.makepyfile(
            test_sample="""
            def function_with_special_chars():
                # Function with unicode and special characters
                message = "Hello, 世界! 🌍"
                return message

            def test_special_chars():
                result = function_with_special_chars()
                assert "世界" in result
                assert "🌍" in result
            """
        )

        with CoverageReportUploadCapture.capture() as upload_capture:
            mock_client = mock_api_client_settings(
                coverage_report_upload_enabled=True, coverage_upload_capture=upload_capture
            )

            result = run_test_with_mocks(
                pytester,
                monkeypatch,
                mock_client,
                COVERAGE_UPLOAD_ENABLED_ENV,
                "1",
                ["--ddtrace", "--cov", "-v", "-s"],
            )

        assert result.ret == 0

        coverage_uploads = upload_capture.get_coverage_report_uploads()
        assert len(coverage_uploads) == 1

        # Verify LCOV can handle files with special characters
        lcov_content = upload_capture.get_lcov_content(coverage_uploads[0])

        # Basic LCOV structure should be intact
        assert "SF:" in lcov_content
        assert "DA:" in lcov_content
        assert "end_of_record" in lcov_content

        # Should be valid UTF-8
        assert isinstance(lcov_content, str)

        # Try encoding/decoding to ensure it's valid
        encoded = lcov_content.encode("utf-8")
        decoded = encoded.decode("utf-8")
        assert decoded == lcov_content
