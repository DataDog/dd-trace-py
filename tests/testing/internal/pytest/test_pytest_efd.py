from __future__ import annotations

import typing as t
from unittest.mock import patch

from _pytest.pytester import Pytester
import pytest

from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef
from tests.testing.internal.pytest.utils import assert_stats
from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


class TestEFD:
    def test_efd_one_new_test(self, pytester: Pytester) -> None:
        """Test that EFD retries new tests and not known tests."""
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
        assert_stats(result, passed=2)

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

        [session_event] = event_capture.events_by_type("test_session_end")
        assert session_event["content"]["meta"].get("test.early_flake.abort_reason") is None

    def test_efd_slow_test(self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that EFD abort reason is marked on slow tests."""
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

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    efd_enabled=True, known_tests_enabled=True, known_tests=known_tests
                ),
            ),
            # Simulate test that takes more than 5 minutes to run.
            patch("ddtrace.testing.internal.test_data.Test.seconds_so_far", return_value=301),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0
        assert_stats(result, passed=1)

        # There should be events for 1 test, 1 suite, 1 module, 1 session
        assert len(list(event_capture.events())) == 4

        efd_tests = list(event_capture.events_by_test_name("test_new"))
        assert len(efd_tests) == 1

        assert efd_tests[0]["content"]["meta"].get("test.status") == "pass"
        assert efd_tests[0]["content"]["meta"].get("test.is_new") == "true"
        assert efd_tests[0]["content"]["meta"].get("test.is_retry") is None
        assert efd_tests[0]["content"]["meta"].get("test.early_flake.abort_reason") == "slow"

        [session_event] = event_capture.events_by_type("test_session_end")
        assert session_event["content"]["meta"].get("test.early_flake.abort_reason") is None

    def test_efd_not_so_slow_test(self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that EFD retries fewer times if the test takes longer."""
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

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    efd_enabled=True, known_tests_enabled=True, known_tests=known_tests
                ),
            ),
            # slow_test_retries_10s is 5, so a test that takes 8 seconds should have 1 initial attempt + 5 retries.
            patch("ddtrace.testing.internal.test_data.TestRun.seconds_so_far", return_value=8),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0
        assert_stats(result, passed=1)

        # There should be events for 6 tests, 1 suite, 1 module, 1 session
        assert len(list(event_capture.events())) == 9

        efd_tests = list(event_capture.events_by_test_name("test_new"))
        assert len(efd_tests) == 6

        [session_event] = event_capture.events_by_type("test_session_end")
        assert session_event["content"]["meta"].get("test.early_flake.abort_reason") is None

    def test_efd_no_known_tests(self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that EFD is disabled when there are no known tests."""
        pytester.makepyfile(
            test_foo="""
            def test_new():
                '''A test that should be retried by EFD.'''
                assert True
        """
        )

        known_tests: t.Set[t.Union[TestRef, SuiteRef]] = {}

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
        assert_stats(result, passed=1)

        # There should be events for 1 test, 1 suite, 1 module, 1 session
        assert len(list(event_capture.events())) == 4

        efd_tests = list(event_capture.events_by_test_name("test_new"))
        assert len(efd_tests) == 1

        assert efd_tests[0]["content"]["meta"].get("test.status") == "pass"
        assert efd_tests[0]["content"]["meta"].get("test.is_new") is None
        assert efd_tests[0]["content"]["meta"].get("test.is_retry") is None

        [session_event] = event_capture.events_by_type("test_session_end")
        assert session_event["content"]["meta"].get("test.early_flake.abort_reason") is None

    def test_efd_faulty_session(self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that session is marked as faulty if there are too many new tests."""
        lots_of_new_tests = "".join(
            f"""
            def test_{i}():
                assert True
            """
            for i in range(100)
        )

        pytester.makepyfile(test_foo=lots_of_new_tests)

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
        assert_stats(result, passed=100)

        # There should be events for 100 test, 1 suite, 1 module, 1 session
        assert len(list(event_capture.events())) == 103

        [session_event] = event_capture.events_by_type("test_session_end")
        assert session_event["content"]["meta"].get("test.early_flake.abort_reason") == "faulty"

    def test_efd_pass_fail(self, pytester: Pytester, capsys: pytest.CaptureFixture) -> None:
        """Test that a test that passes and fails is logged as flaky."""
        pytester.makepyfile(
            test_foo="""
            class TestFlaky:
                count = 0
                def test_new(self):
                     self.count += 1
                     assert self.count <= 1
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
                result = pytester.inline_run("--ddtrace", "-v")

        assert result.ret == 0
        assert_stats(result, flaky=1)

        # There should be events for 1 new test + 10 retries, test, 1 suite, 1 module, 1 session
        assert len(list(event_capture.events())) == 14

        efd_tests = list(event_capture.events_by_test_name("TestFlaky::test_new"))
        assert len(efd_tests) == 11

        assert efd_tests[0]["content"]["meta"].get("test.status") == "pass"
        assert efd_tests[0]["content"]["meta"].get("test.is_new") == "true"
        assert efd_tests[0]["content"]["meta"].get("test.is_retry") is None

        for i in range(1, len(efd_tests)):
            assert efd_tests[i]["content"]["meta"].get("test.status") == "fail"
            assert efd_tests[i]["content"]["meta"].get("test.is_new") == "true"
            assert efd_tests[i]["content"]["meta"].get("test.is_retry") == "true"
            assert efd_tests[i]["content"]["meta"].get("test.retry_reason") == "early_flake_detection"

        assert efd_tests[i]["content"]["meta"].get("test.retry_reason") == "early_flake_detection"

        [session_event] = event_capture.events_by_type("test_session_end")
        assert session_event["content"]["meta"].get("test.early_flake.abort_reason") is None

        output = capsys.readouterr()
        assert output.out.count("test_foo.py::TestFlaky::test_new RETRY PASSED (Early Flake Detection)") == 1
        assert output.out.count("test_foo.py::TestFlaky::test_new RETRY FAILED (Early Flake Detection)") == 10
        assert output.out.count("test_foo.py::TestFlaky::test_new FLAKY") == 1
        # Test failure is reported.
        assert output.out.count("FAILED test_foo.py::TestFlaky::test_new - assert 2 <= 1") == 1
        # Check final stats.
        assert output.out.count("=== 1 flaky in ") == 1

    def test_efd_skip_fail(self, pytester: Pytester, capsys: pytest.CaptureFixture) -> None:
        """Test that a test that skips and fails is logged as failed."""
        pytester.makepyfile(
            test_foo="""
            import pytest

            class TestFlaky:
                count = 0
                def test_new(self):
                     self.count += 1
                     if self.count <= 1:
                         pytest.skip()
                     else:
                         assert False
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
                result = pytester.inline_run("--ddtrace", "-v")

        assert result.ret == 1
        assert_stats(result, failed=1)

        # There should be events for 1 new test + 10 retries, test, 1 suite, 1 module, 1 session
        assert len(list(event_capture.events())) == 14

        efd_tests = list(event_capture.events_by_test_name("TestFlaky::test_new"))
        assert len(efd_tests) == 11

        assert efd_tests[0]["content"]["meta"].get("test.status") == "skip"
        assert efd_tests[0]["content"]["meta"].get("test.is_new") == "true"
        assert efd_tests[0]["content"]["meta"].get("test.is_retry") is None

        for i in range(1, len(efd_tests)):
            assert efd_tests[i]["content"]["meta"].get("test.status") == "fail"
            assert efd_tests[i]["content"]["meta"].get("test.is_new") == "true"
            assert efd_tests[i]["content"]["meta"].get("test.is_retry") == "true"
            assert efd_tests[i]["content"]["meta"].get("test.retry_reason") == "early_flake_detection"

        assert efd_tests[i]["content"]["meta"].get("test.retry_reason") == "early_flake_detection"

        [session_event] = event_capture.events_by_type("test_session_end")
        assert session_event["content"]["meta"].get("test.early_flake.abort_reason") is None

        output = capsys.readouterr()
        assert output.out.count("test_foo.py::TestFlaky::test_new RETRY SKIPPED (Early Flake Detection)") == 1
        assert output.out.count("test_foo.py::TestFlaky::test_new RETRY FAILED (Early Flake Detection)") == 10
        assert output.out.count("test_foo.py::TestFlaky::test_new FAILED") == 1
        # Test failure is reported.
        assert output.out.count("FAILED test_foo.py::TestFlaky::test_new - assert False") == 1
        # Check final stats.
        assert output.out.count("=== 1 failed in ") == 1

    def test_efd_skip_fail_pass(self, pytester: Pytester, capsys: pytest.CaptureFixture) -> None:
        """Test that a test that skips, fails and passes is logged as flaky."""
        pytester.makepyfile(
            test_foo="""
            import pytest

            class TestFlaky:
                count = 0
                def test_new(self):
                     self.count += 1
                     if self.count <= 1:
                         pytest.skip()
                     elif self.count <= 2:
                         assert False
                     else:
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
                result = pytester.inline_run("--ddtrace", "-v")

        assert result.ret == 0
        assert_stats(result, flaky=1)

        # There should be events for 1 new test + 10 retries, test, 1 suite, 1 module, 1 session
        assert len(list(event_capture.events())) == 14

        efd_tests = list(event_capture.events_by_test_name("TestFlaky::test_new"))
        assert len(efd_tests) == 11

        assert efd_tests[0]["content"]["meta"].get("test.status") == "skip"
        assert efd_tests[0]["content"]["meta"].get("test.is_new") == "true"
        assert efd_tests[0]["content"]["meta"].get("test.is_retry") is None

        assert efd_tests[1]["content"]["meta"].get("test.status") == "fail"
        assert efd_tests[1]["content"]["meta"].get("test.is_new") == "true"
        assert efd_tests[1]["content"]["meta"].get("test.is_retry") == "true"
        assert efd_tests[1]["content"]["meta"].get("test.retry_reason") == "early_flake_detection"

        for i in range(2, len(efd_tests)):
            assert efd_tests[i]["content"]["meta"].get("test.status") == "pass"
            assert efd_tests[i]["content"]["meta"].get("test.is_new") == "true"
            assert efd_tests[i]["content"]["meta"].get("test.is_retry") == "true"
            assert efd_tests[i]["content"]["meta"].get("test.retry_reason") == "early_flake_detection"

        assert efd_tests[i]["content"]["meta"].get("test.retry_reason") == "early_flake_detection"

        [session_event] = event_capture.events_by_type("test_session_end")
        assert session_event["content"]["meta"].get("test.early_flake.abort_reason") is None

        output = capsys.readouterr()
        assert output.out.count("test_foo.py::TestFlaky::test_new RETRY SKIPPED (Early Flake Detection)") == 1
        assert output.out.count("test_foo.py::TestFlaky::test_new RETRY FAILED (Early Flake Detection)") == 1
        assert output.out.count("test_foo.py::TestFlaky::test_new RETRY PASSED (Early Flake Detection)") == 9
        assert output.out.count("test_foo.py::TestFlaky::test_new FLAKY") == 1
        # Test failure is reported.
        assert output.out.count("FAILED test_foo.py::TestFlaky::test_new - assert False") == 1
        # Check final stats.
        assert output.out.count("=== 1 flaky in ") == 1
