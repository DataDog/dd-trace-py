from __future__ import annotations

import typing as t
from unittest.mock import patch

from _pytest.pytester import Pytester
import pytest

from ddtrace.testing.internal.retry_handlers import EarlyFlakeDetectionHandler
from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef
from tests.testing.internal.pytest.utils import assert_stats
from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


class TestEFD:
    def test_efd_one_new_test(self, pytester: Pytester) -> None:
        # Create a test file with multiple tests
        pytester.makepyfile(
            test_foo="""
            def test_known():
                '''A test that should not be retried by EFD.'''
                assert True

            def test_new():
                '''A test that should be retried by EFD.'''
                assert True
        """
        )

        known_tests: t.Set[t.Union[TestRef, SuiteRef]] = {
            TestRef(SuiteRef(ModuleRef("."), "test_foo.py"), "test_known"),
        }

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    efd_enabled=True, known_tests_enabled=True, known_tests=known_tests
                ),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0
        assert_stats(result, passed=2, dd_retry=11)

        # There should be events for 1 new test + 10 retries + 1 known test, 1 suite, 1 module, 1 session
        assert len(list(event_capture.events())) == 15

        efd_tests = list(event_capture.events_by_test_name("test_new"))
        assert len(efd_tests) == 11

        assert efd_tests[0]["content"]["meta"].get("test.status") == "pass"
        assert efd_tests[0]["content"]["meta"].get("test.is_new") == "true"
        assert efd_tests[0]["content"]["meta"].get("test.is_retry") is None

        for i in range(1, len(efd_tests)):
            assert efd_tests[i]["content"]["meta"].get("test.status") == "pass"
            assert efd_tests[i]["content"]["meta"].get("test.is_new") == "true"
            assert efd_tests[i]["content"]["meta"].get("test.is_retry") == "true"
            assert efd_tests[i]["content"]["meta"].get("test.retry_reason") == "early_flake_detection"

        assert efd_tests[i]["content"]["meta"].get("test.retry_reason") == "early_flake_detection"

        known_tests = list(event_capture.events_by_test_name("test_known"))
        assert len(known_tests) == 1

        assert known_tests[0]["content"]["meta"].get("test.status") == "pass"
        assert known_tests[0]["content"]["meta"].get("test.is_new") is None
        assert known_tests[0]["content"]["meta"].get("test.is_retry") is None

    def test_efd_slow_test(self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
        # Create a test file with multiple tests
        pytester.makepyfile(
            test_foo="""
            def test_new():
                '''A test that should be retried by EFD.'''
                assert True
        """
        )

        known_tests: t.Set[t.Union[TestRef, SuiteRef]] = {
            TestRef(SuiteRef(ModuleRef("."), "test_foo.py"), "test_known"),
        }

        monkeypatch.setattr(EarlyFlakeDetectionHandler, "EFD_ABORT_TEST_SECONDS", -1)  # Force all tests to abort EFD.

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    efd_enabled=True, known_tests_enabled=True, known_tests=known_tests
                ),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0
        assert_stats(result, passed=1, dd_retry=0)

        # There should be events for 1 test, 1 suite, 1 module, 1 session
        assert len(list(event_capture.events())) == 4

        efd_tests = list(event_capture.events_by_test_name("test_new"))
        assert len(efd_tests) == 1

        assert efd_tests[0]["content"]["meta"].get("test.status") == "pass"
        assert efd_tests[0]["content"]["meta"].get("test.is_new") == "true"
        assert efd_tests[0]["content"]["meta"].get("test.is_retry") is None
        assert efd_tests[0]["content"]["meta"].get("test.early_flake.abort_reason") == "slow"
