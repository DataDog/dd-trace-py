from __future__ import annotations

import typing as t
from unittest.mock import patch

from _pytest.pytester import Pytester

from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef
from tests.testing.internal.pytest.utils import assert_stats
from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


class TestATR:
    def test_atr_passing_test_not_retried(self, pytester: Pytester) -> None:
        """Test that a passing test is not retried by ATR."""
        pytester.makepyfile(
            test_foo="""
            def test_pass():
                assert True
        """
        )

        known_tests: set[t.Union[TestRef, SuiteRef]] = {
            TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_pass"),
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
        assert_stats(result, passed=1)

        # 1 test + 1 suite + 1 module + 1 session = 4 events, no retries
        assert len(list(event_capture.events())) == 4

        test_events = list(event_capture.events_by_test_name("test_pass"))
        assert len(test_events) == 1
        assert test_events[0]["content"]["meta"].get("test.status") == "pass"
        assert test_events[0]["content"]["meta"].get("test.is_retry") is None

    def test_atr_failing_test_retried(self, pytester: Pytester) -> None:
        """Test that a failing test is retried by ATR."""
        pytester.makepyfile(
            test_foo="""
            def test_fail():
                assert False
        """
        )

        known_tests: set[t.Union[TestRef, SuiteRef]] = {
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

        test_events = list(event_capture.events_by_test_name("test_fail"))
        # 1 initial + 5 retries (default max)
        assert len(test_events) == 6

        assert test_events[0]["content"]["meta"].get("test.status") == "fail"
        assert test_events[0]["content"]["meta"].get("test.is_retry") is None

        for i in range(1, len(test_events)):
            assert test_events[i]["content"]["meta"].get("test.status") == "fail"
            assert test_events[i]["content"]["meta"].get("test.is_retry") == "true"
            assert test_events[i]["content"]["meta"].get("test.retry_reason") == "auto_test_retry"

    def test_atr_early_exit_after_first_pass(self, pytester: Pytester) -> None:
        """Test that ATR stops retrying after the first successful execution.

        A flaky test that fails initially but passes on retry should not be retried further,
        even if the maximum retry count has not been reached.
        """
        pytester.makepyfile(
            test_foo="""
            class TestFlaky:
                count = 0
                def test_flaky(self):
                    TestFlaky.count += 1
                    # Fails on first attempt, passes on second
                    assert TestFlaky.count > 1
        """
        )

        known_tests: set[t.Union[TestRef, SuiteRef]] = {
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
        # 1 initial (fail) + 1 retry (pass) = 2 events, NOT 1 + 5 retries
        assert len(test_events) == 2

        assert test_events[0]["content"]["meta"].get("test.status") == "fail"
        assert test_events[0]["content"]["meta"].get("test.is_retry") is None

        assert test_events[1]["content"]["meta"].get("test.status") == "pass"
        assert test_events[1]["content"]["meta"].get("test.is_retry") == "true"
        assert test_events[1]["content"]["meta"].get("test.retry_reason") == "auto_test_retry"
