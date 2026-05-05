from __future__ import annotations

from unittest.mock import patch

from _pytest.pytester import Pytester
import pytest

from ddtrace.testing.internal.settings_data import TestProperties
from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef
from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


class TestAttemptToFix:
    def test_atf_passing_test_retried_fully(self, pytester: Pytester) -> None:
        """Test that an always-passing attempt-to-fix test is retried the full number of times."""
        pytester.makepyfile(
            test_foo="""
            def test_fixed():
                assert True
        """
        )

        test_ref = TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_fixed")
        known_tests: set[TestRef] = {test_ref}
        test_properties = {test_ref: TestProperties(attempt_to_fix=True)}

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    test_management_enabled=True,
                    known_tests_enabled=True,
                    known_tests=known_tests,
                    test_management_properties=test_properties,
                ),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0

        test_events = list(event_capture.events_by_test_name("test_fixed"))
        # 1 initial + 20 retries (default attempt_to_fix_retries) = 21 events
        assert len(test_events) == 21

        assert test_events[0]["content"]["meta"].get("test.status") == "pass"
        assert test_events[0]["content"]["meta"].get("test.is_retry") is None

        for i in range(1, len(test_events)):
            assert test_events[i]["content"]["meta"].get("test.status") == "pass"
            assert test_events[i]["content"]["meta"].get("test.is_retry") == "true"
            assert test_events[i]["content"]["meta"].get("test.retry_reason") == "attempt_to_fix"

        # All retries passed, so the test should be marked as attempt_to_fix_passed
        assert test_events[-1]["content"]["meta"].get("test.test_management.attempt_to_fix_passed") == "true"

    def test_atf_early_exit_after_first_failure(self, pytester: Pytester) -> None:
        """Test that ATF stops retrying after the first failed execution.

        An attempt-to-fix test that passes initially but then fails should not be retried further,
        even if the maximum retry count has not been reached. This avoids wasting CI time when the
        fix is clearly not working.
        """
        pytester.makepyfile(
            test_foo="""
            class TestNotFixed:
                count = 0
                def test_broken(self):
                    TestNotFixed.count += 1
                    # Passes on first attempt, fails on second
                    assert TestNotFixed.count <= 1
        """
        )

        test_ref = TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "TestNotFixed::test_broken")
        known_tests: set[TestRef] = {test_ref}
        test_properties = {test_ref: TestProperties(attempt_to_fix=True)}

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    test_management_enabled=True,
                    known_tests_enabled=True,
                    known_tests=known_tests,
                    test_management_properties=test_properties,
                ),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 1

        test_events = list(event_capture.events_by_test_name("TestNotFixed::test_broken"))
        # 1 initial (pass) + 1 retry (fail) = 2 events, NOT 1 + 20 retries
        assert len(test_events) == 2

        assert test_events[0]["content"]["meta"].get("test.status") == "pass"
        assert test_events[0]["content"]["meta"].get("test.is_retry") is None

        assert test_events[1]["content"]["meta"].get("test.status") == "fail"
        assert test_events[1]["content"]["meta"].get("test.is_retry") == "true"
        assert test_events[1]["content"]["meta"].get("test.retry_reason") == "attempt_to_fix"
        assert test_events[1]["content"]["meta"].get("test.test_management.attempt_to_fix_passed") == "false"

    def test_atf_always_failing_exits_after_first(self, pytester: Pytester) -> None:
        """Test that ATF stops retrying an always-failing test after the first run."""
        pytester.makepyfile(
            test_foo="""
            def test_always_fails():
                assert False
        """
        )

        test_ref = TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_always_fails")
        known_tests: set[TestRef] = {test_ref}
        test_properties = {test_ref: TestProperties(attempt_to_fix=True)}

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    test_management_enabled=True,
                    known_tests_enabled=True,
                    known_tests=known_tests,
                    test_management_properties=test_properties,
                ),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                pytester.inline_run("--ddtrace", "-v", "-s")

        test_events = list(event_capture.events_by_test_name("test_always_fails"))
        # 1 initial (fail) = 1 event only, no retries since first run already failed
        assert len(test_events) == 1

        assert test_events[0]["content"]["meta"].get("test.status") == "fail"
        assert test_events[0]["content"]["meta"].get("test.is_retry") is None
        # Final ATF tags must still be set even without retries
        assert test_events[0]["content"]["meta"].get("test.test_management.attempt_to_fix_passed") == "false"
        assert test_events[0]["content"]["meta"].get("test.has_failed_all_retries") == "true"

    @pytest.mark.parametrize(
        "test_properties",
        [
            TestProperties(quarantined=True, attempt_to_fix=True),
            TestProperties(disabled=True, attempt_to_fix=True),
        ],
    )
    def test_atf_failures_are_not_masked_by_quarantine_or_disable(
        self, pytester: Pytester, test_properties: TestProperties
    ) -> None:
        """Test that ATF takes precedence over quarantine/disable and failed attempts fail pytest."""
        pytester.makepyfile(
            test_foo="""
            def test_not_fixed():
                assert False
        """
        )

        test_ref = TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_not_fixed")
        known_tests: set[TestRef] = {test_ref}

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    test_management_enabled=True,
                    known_tests_enabled=True,
                    known_tests=known_tests,
                    test_management_properties={test_ref: test_properties},
                ),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 1

        test_events = list(event_capture.events_by_test_name("test_not_fixed"))
        assert len(test_events) == 1
        assert test_events[0]["content"]["meta"].get("test.status") == "fail"
        assert test_events[0]["content"]["meta"].get("test.test_management.attempt_to_fix_passed") == "false"
