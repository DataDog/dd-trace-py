"""End-to-end tests for dynamic Auto Test Retries (ATR).

These tests drive the real pytest plugin through ``pytester.inline_run`` with the
``DD_CIVISIBILITY_DYNAMIC_ATR_ENABLED`` feature flag set, and assert that the number
of retries is driven by the test's initial-attempt duration bucket rather than the
flat ``DD_CIVISIBILITY_FLAKY_RETRY_COUNT`` limit.
"""

from __future__ import annotations

from unittest.mock import patch

from _pytest.pytester import Pytester

from ddtrace.testing.internal.dynamic_atr_retries import DYNAMIC_ATR_BUCKETS_ENV
from ddtrace.testing.internal.dynamic_atr_retries import DYNAMIC_ATR_ENABLED_ENV
from ddtrace.testing.internal.settings_data import AutoTestRetriesSettings
from ddtrace.testing.internal.settings_data import EarlyFlakeDetectionSettings
from ddtrace.testing.internal.settings_data import Settings
from ddtrace.testing.internal.telemetry import TelemetryAPI
from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef
from tests.testing.internal.pytest.utils import assert_stats
from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


def _assert_retry_events(event_capture: EventCapture, test_name: str, expected_retries: int) -> None:
    """Assert a failing test produced one initial run plus ``expected_retries`` ATR retries."""
    test_events = list(event_capture.events_by_test_name(test_name))
    assert len(test_events) == 1 + expected_retries

    assert test_events[0]["content"]["meta"].get("test.status") == "fail"
    assert test_events[0]["content"]["meta"].get("test.is_retry") is None

    for i in range(1, len(test_events)):
        assert test_events[i]["content"]["meta"].get("test.status") == "fail"
        assert test_events[i]["content"]["meta"].get("test.is_retry") == "true"
        assert test_events[i]["content"]["meta"].get("test.retry_reason") == "auto_test_retry"


def _settings_with_efd_buckets(slow_5s: int, slow_10s: int = 1, slow_30s: int = 1, slow_5m: int = 1) -> Settings:
    return Settings(
        early_flake_detection=EarlyFlakeDetectionSettings(
            enabled=False,
            slow_test_retries_5s=slow_5s,
            slow_test_retries_10s=slow_10s,
            slow_test_retries_30s=slow_30s,
            slow_test_retries_5m=slow_5m,
        ),
        auto_test_retries=AutoTestRetriesSettings(enabled=True),
        known_tests_enabled=True,
    )


class TestDynamicATR:
    def test_dynamic_atr_custom_buckets_retries_fast_test(self, pytester: Pytester, monkeypatch) -> None:
        """A fast failing test is retried by the 5s custom bucket budget, not the flat limit."""
        monkeypatch.setenv(DYNAMIC_ATR_ENABLED_ENV, "true")
        monkeypatch.setenv(DYNAMIC_ATR_BUCKETS_ENV, "3,1,1,1,1")
        # Set a flat limit that differs from the bucket value to prove the bucket wins.
        monkeypatch.setenv("DD_CIVISIBILITY_FLAKY_RETRY_COUNT", "5")
        pytester.makepyfile(
            test_foo="""
            def test_fail():
                assert False
        """
        )

        known_tests: set[TestRef] = {
            TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_fail"),
        }

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    auto_retries_enabled=True, known_tests_enabled=True, known_tests=known_tests
                ),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 1
        assert_stats(result, failed=1)
        # 5s bucket -> 3 retries, ignoring the flat limit of 5.
        _assert_retry_events(event_capture, "test_fail", expected_retries=3)

    def test_dynamic_atr_uses_efd_buckets_when_no_custom_buckets(self, pytester: Pytester, monkeypatch) -> None:
        """Without custom buckets, the EFD slow-test-retry settings drive the budget."""
        monkeypatch.setenv(DYNAMIC_ATR_ENABLED_ENV, "true")
        monkeypatch.delenv(DYNAMIC_ATR_BUCKETS_ENV, raising=False)
        pytester.makepyfile(
            test_foo="""
            def test_fail():
                assert False
        """
        )

        known_tests: set[TestRef] = {
            TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_fail"),
        }
        mock_client = mock_api_client_settings(
            auto_retries_enabled=True, known_tests_enabled=True, known_tests=known_tests
        )
        mock_client.get_settings.return_value = _settings_with_efd_buckets(slow_5s=2)

        with (
            patch("ddtrace.testing.internal.session_manager.APIClient", return_value=mock_client),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 1
        assert_stats(result, failed=1)
        # EFD 5s bucket -> 2 retries.
        _assert_retry_events(event_capture, "test_fail", expected_retries=2)

    def test_dynamic_atr_invalid_buckets_falls_back_to_efd(self, pytester: Pytester, monkeypatch) -> None:
        """A misconfigured buckets env var degrades to the EFD retry settings rather than failing."""
        monkeypatch.setenv(DYNAMIC_ATR_ENABLED_ENV, "true")
        monkeypatch.setenv(DYNAMIC_ATR_BUCKETS_ENV, "not,enough,values")
        pytester.makepyfile(
            test_foo="""
            def test_fail():
                assert False
        """
        )

        known_tests: set[TestRef] = {
            TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_fail"),
        }
        mock_client = mock_api_client_settings(
            auto_retries_enabled=True, known_tests_enabled=True, known_tests=known_tests
        )
        mock_client.get_settings.return_value = _settings_with_efd_buckets(slow_5s=2)

        with (
            patch("ddtrace.testing.internal.session_manager.APIClient", return_value=mock_client),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 1
        assert_stats(result, failed=1)
        # Invalid buckets -> fall back to EFD 5s bucket -> 2 retries.
        _assert_retry_events(event_capture, "test_fail", expected_retries=2)

    def test_dynamic_atr_disabled_uses_flat_limit(self, pytester: Pytester, monkeypatch) -> None:
        """With the flag off, ATR stays on the flat-limit path."""
        monkeypatch.delenv(DYNAMIC_ATR_ENABLED_ENV, raising=False)
        monkeypatch.setenv("DD_CIVISIBILITY_FLAKY_RETRY_COUNT", "2")
        pytester.makepyfile(
            test_foo="""
            def test_fail():
                assert False
        """
        )

        known_tests: set[TestRef] = {
            TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_fail"),
        }

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    auto_retries_enabled=True, known_tests_enabled=True, known_tests=known_tests
                ),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 1
        assert_stats(result, failed=1)
        # Flat limit of 2 -> 2 retries.
        _assert_retry_events(event_capture, "test_fail", expected_retries=2)

    def test_dynamic_atr_stops_after_first_pass(self, pytester: Pytester, monkeypatch) -> None:
        """Dynamic ATR stops retrying as soon as a retry passes, even with budget remaining."""
        monkeypatch.setenv(DYNAMIC_ATR_ENABLED_ENV, "true")
        monkeypatch.setenv(DYNAMIC_ATR_BUCKETS_ENV, "5,1,1,1,1")
        pytester.makepyfile(
            test_foo="""
            class TestFlaky:
                count = 0
                def test_flaky(self):
                    TestFlaky.count += 1
                    assert TestFlaky.count > 1
        """
        )

        known_tests: set[TestRef] = {
            TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "TestFlaky::test_flaky"),
        }

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    auto_retries_enabled=True, known_tests_enabled=True, known_tests=known_tests
                ),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0
        test_events = list(event_capture.events_by_test_name("TestFlaky::test_flaky"))
        # 1 initial (fail) + 1 retry (pass) = 2 events, NOT 1 + 5.
        assert len(test_events) == 2
        assert test_events[0]["content"]["meta"].get("test.status") == "fail"
        assert test_events[0]["content"]["meta"].get("test.is_retry") is None
        assert test_events[1]["content"]["meta"].get("test.status") == "pass"
        assert test_events[1]["content"]["meta"].get("test.is_retry") == "true"
        assert test_events[1]["content"]["meta"].get("test.retry_reason") == "auto_test_retry"

    def test_dynamic_atr_records_telemetry_with_custom_buckets(self, pytester: Pytester, monkeypatch) -> None:
        """Enabling dynamic ATR with custom buckets records telemetry with has_custom_buckets=True."""
        monkeypatch.setenv(DYNAMIC_ATR_ENABLED_ENV, "true")
        monkeypatch.setenv(DYNAMIC_ATR_BUCKETS_ENV, "3,1,1,1,1")
        pytester.makepyfile(
            test_foo="""
            def test_pass():
                assert True
        """
        )

        known_tests: set[TestRef] = {
            TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_pass"),
        }

        with patch.object(TelemetryAPI, "record_dynamic_atr_retries") as telemetry_mock:
            with (
                patch(
                    "ddtrace.testing.internal.session_manager.APIClient",
                    return_value=mock_api_client_settings(
                        auto_retries_enabled=True, known_tests_enabled=True, known_tests=known_tests
                    ),
                ),
                setup_standard_mocks(),
            ):
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0
        telemetry_mock.assert_called_once_with(True)

    def test_dynamic_atr_records_telemetry_without_custom_buckets(self, pytester: Pytester, monkeypatch) -> None:
        """Enabling dynamic ATR without custom buckets records telemetry with has_custom_buckets=False."""
        monkeypatch.setenv(DYNAMIC_ATR_ENABLED_ENV, "true")
        monkeypatch.delenv(DYNAMIC_ATR_BUCKETS_ENV, raising=False)
        pytester.makepyfile(
            test_foo="""
            def test_pass():
                assert True
        """
        )

        known_tests: set[TestRef] = {
            TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_pass"),
        }

        with patch.object(TelemetryAPI, "record_dynamic_atr_retries") as telemetry_mock:
            with (
                patch(
                    "ddtrace.testing.internal.session_manager.APIClient",
                    return_value=mock_api_client_settings(
                        auto_retries_enabled=True, known_tests_enabled=True, known_tests=known_tests
                    ),
                ),
                setup_standard_mocks(),
            ):
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0
        telemetry_mock.assert_called_once_with(False)

    def test_dynamic_atr_disabled_does_not_record_telemetry(self, pytester: Pytester, monkeypatch) -> None:
        """When dynamic ATR is off, the telemetry metric is not recorded."""
        monkeypatch.delenv(DYNAMIC_ATR_ENABLED_ENV, raising=False)
        pytester.makepyfile(
            test_foo="""
            def test_pass():
                assert True
        """
        )

        known_tests: set[TestRef] = {
            TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_pass"),
        }

        with patch.object(TelemetryAPI, "record_dynamic_atr_retries") as telemetry_mock:
            with (
                patch(
                    "ddtrace.testing.internal.session_manager.APIClient",
                    return_value=mock_api_client_settings(
                        auto_retries_enabled=True, known_tests_enabled=True, known_tests=known_tests
                    ),
                ),
                setup_standard_mocks(),
            ):
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0
        telemetry_mock.assert_not_called()
