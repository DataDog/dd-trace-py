"""Test that quarantined tests interact correctly with ATR (Auto Test Retry).

Verifies that when a test is quarantined and ATR is enabled, ATR retries the
failing test, and quarantine masking is applied afterwards so the session passes.
"""

from __future__ import annotations

from unittest.mock import patch

from _pytest.pytester import Pytester

from ddtrace.testing.internal.settings_data import TestProperties
from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef
from tests.testing.internal.pytest.utils import assert_stats
from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


class TestQuarantineWithATR:
    def test_quarantined_failing_test_is_retried_by_atr(self, pytester: Pytester) -> None:
        """When a test is quarantined and ATR is enabled, a failing test should be retried by ATR.

        The quarantine masking is deferred until after ATR retries, so ATR sees the real FAIL
        status and retries up to the default max (5 retries = 6 total runs).
        The test session should still pass (exit code 0) with the test shown as quarantined.
        """
        pytester.makepyfile(
            test_foo="""
            def test_quarantined_fail():
                assert False
        """
        )

        test_ref = TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_quarantined_fail")
        known_tests: set[TestRef] = {test_ref}

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    auto_retries_enabled=True,
                    test_management_enabled=True,
                    known_tests_enabled=True,
                    known_tests=known_tests,
                    test_management_properties={test_ref: TestProperties(quarantined=True)},
                ),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        # Quarantined tests should NOT cause a session failure
        assert result.ret == 0

        # Should show as quarantined, not failed
        assert_stats(result, quarantined=1)

        # ATR should have retried: 1 initial + 5 retries = 6 test events
        test_events = list(event_capture.events_by_test_name("test_quarantined_fail"))
        assert len(test_events) == 6, f"Expected 6 test events (1 initial + 5 retries), got {len(test_events)}"

        # First run: fail, not a retry
        assert test_events[0]["content"]["meta"].get("test.status") == "fail"
        assert test_events[0]["content"]["meta"].get("test.is_retry") is None

        # Retries: all fail, marked as retries with ATR reason
        for i in range(1, len(test_events)):
            assert test_events[i]["content"]["meta"].get("test.status") == "fail"
            assert test_events[i]["content"]["meta"].get("test.is_retry") == "true"
            assert test_events[i]["content"]["meta"].get("test.retry_reason") == "auto_test_retry"
