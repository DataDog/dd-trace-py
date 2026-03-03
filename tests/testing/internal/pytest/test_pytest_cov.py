from unittest.mock import patch

from _pytest.pytester import Pytester
import pytest
from pytest import MonkeyPatch

from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


COVERAGE_UPLOAD_ENABLED_ENV = "DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED"


class TestPytestCovPercentage:
    @pytest.fixture(autouse=True)
    def isolate_coverage_upload_env(self, monkeypatch: MonkeyPatch) -> None:
        """Unset coverage upload env var so tests are not affected by external environment."""
        monkeypatch.delenv(COVERAGE_UPLOAD_ENABLED_ENV, raising=False)

    def test_pytest_cov_percentage_enabled(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        pytester.makepyfile(
            test_foo="""
            def test_ok():
                assert True
        """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                pytester.inline_run("--ddtrace", "--cov", "-v", "-s")

        [session_event] = event_capture.events_by_type("test_session_end")
        assert isinstance(session_event["content"]["metrics"].get("test.code_coverage.lines_pct"), float)

    def test_pytest_cov_percentage_disabled(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        pytester.makepyfile(
            test_foo="""
            def test_ok():
                assert True
        """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                pytester.inline_run("--ddtrace", "-v", "-s")

        [session_event] = event_capture.events_by_type("test_session_end")
        assert session_event["content"]["metrics"].get("test.code_coverage.lines_pct") is None
