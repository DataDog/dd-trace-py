from __future__ import annotations

import contextlib
import os
import typing as t
from unittest.mock import patch

from _pytest.pytester import Pytester

from tests.testing.internal.pytest.utils import assert_stats
from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


@contextlib.contextmanager
def out_of_session_retries_enabled() -> t.Iterator[None]:
    """Re-enable out-of-session retries (disabled by default in ``setup_standard_mocks``)."""
    with patch.dict(os.environ, {"DD_CIVISIBILITY_OUT_OF_SESSION_RETRIES_ENABLED": "true"}):
        yield


class TestOutOfSessionRetries:
    def test_failing_test_rerun_once_in_new_session(self, pytester: Pytester) -> None:
        """A test that fails is re-run exactly once in a second session, tagged retry_reason=out_of_session."""
        pytester.makepyfile(
            test_foo="""
            def test_fail():
                assert False
        """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
            out_of_session_retries_enabled(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        # OSR does not change pytest's verdict: the original failure stands.
        assert result.ret == 1
        assert_stats(result, failed=1)

        # Two sessions: the original one and the out-of-session retry one.
        assert len(list(event_capture.events_by_type("test_session_end"))) == 2

        test_events = list(event_capture.events_by_test_name("test_fail"))
        # 1 original run + 1 out-of-session retry.
        assert len(test_events) == 2

        assert test_events[0]["content"]["meta"].get("test.status") == "fail"
        assert test_events[0]["content"]["meta"].get("test.is_retry") is None

        assert test_events[1]["content"]["meta"].get("test.status") == "fail"
        assert test_events[1]["content"]["meta"].get("test.is_retry") == "true"
        assert test_events[1]["content"]["meta"].get("test.retry_reason") == "out_of_session"

    def test_passing_test_not_rerun(self, pytester: Pytester) -> None:
        """A passing test does not trigger an out-of-session retry, so no second session is created."""
        pytester.makepyfile(
            test_foo="""
            def test_pass():
                assert True
        """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
            out_of_session_retries_enabled(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0
        assert_stats(result, passed=1)

        assert len(list(event_capture.events_by_type("test_session_end"))) == 1

        test_events = list(event_capture.events_by_test_name("test_pass"))
        assert len(test_events) == 1
        assert test_events[0]["content"]["meta"].get("test.is_retry") is None

    def test_at_most_five_tests_are_rerun(self, pytester: Pytester) -> None:
        """When more than five tests fail, only five are picked for out-of-session retries."""
        pytester.makepyfile(test_foo="\n".join(f"def test_fail_{i}():\n    assert False\n" for i in range(7)))

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
            out_of_session_retries_enabled(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 1
        assert_stats(result, failed=7)

        assert len(list(event_capture.events_by_type("test_session_end"))) == 2

        osr_events = [
            event
            for event in event_capture.events_by_type("test")
            if event["content"]["meta"].get("test.retry_reason") == "out_of_session"
        ]
        assert len(osr_events) == 5

    def test_atr_exhausted_then_rerun_out_of_session(self, pytester: Pytester) -> None:
        """A test that fails every ATR retry is still re-run once out of session."""
        pytester.makepyfile(
            test_foo="""
            def test_fail():
                assert False
        """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(auto_retries_enabled=True),
            ),
            setup_standard_mocks(),
            out_of_session_retries_enabled(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 1

        test_events = list(event_capture.events_by_test_name("test_fail"))
        # 1 initial + 5 ATR retries (in session 1) + 1 out-of-session retry (in session 2).
        assert len(test_events) == 7

        atr_events = [e for e in test_events if e["content"]["meta"].get("test.retry_reason") == "auto_test_retry"]
        osr_events = [e for e in test_events if e["content"]["meta"].get("test.retry_reason") == "out_of_session"]
        assert len(atr_events) == 5
        assert len(osr_events) == 1
        assert osr_events[0]["content"]["meta"].get("test.is_retry") == "true"

    def test_state_leak_passes_when_retried_in_isolation(self, pytester: Pytester) -> None:
        """A test that fails only because an earlier test polluted a session-scoped fixture passes out of session.

        This is the core motivating scenario: the out-of-session retry runs the failed test alone in a fresh session,
        so the polluting test does not run and the session-scoped fixture is recreated clean.
        """
        pytester.makepyfile(
            test_foo="""
            import pytest

            @pytest.fixture(scope="session")
            def shared_resource():
                # Recreated for each session; torn down at session end. The in-session retries reuse the same
                # (polluted) object, but the out-of-session retry gets a fresh one.
                return {"polluted": False}

            def test_a_pollutes(shared_resource):
                shared_resource["polluted"] = True
                assert True

            def test_b_victim(shared_resource):
                # Fails in the main session because test_a polluted the shared resource first; passes out of
                # session because only test_b is re-run, with a fresh shared_resource and without test_a.
                assert shared_resource["polluted"] is False
        """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
            out_of_session_retries_enabled(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s", "-p", "no:randomly")

        # The main session still failed (OSR does not change pytest's verdict).
        assert result.ret == 1
        assert_stats(result, passed=1, failed=1)

        assert len(list(event_capture.events_by_type("test_session_end"))) == 2

        victim_events = list(event_capture.events_by_test_name("test_b_victim"))
        # 1 failing run in the main session + 1 passing out-of-session retry.
        assert len(victim_events) == 2
        assert victim_events[0]["content"]["meta"].get("test.status") == "fail"
        assert victim_events[1]["content"]["meta"].get("test.status") == "pass"
        assert victim_events[1]["content"]["meta"].get("test.retry_reason") == "out_of_session"

        # The polluting test is not re-run out of session.
        assert len(list(event_capture.events_by_test_name("test_a_pollutes"))) == 1

    def test_setup_phase_failure_is_retried_out_of_session(self, pytester: Pytester) -> None:
        """A test that fails during fixture setup (a leaked session-scoped resource) is retried out of session."""
        pytester.makepyfile(
            test_foo="""
            import pytest

            @pytest.fixture(scope="session")
            def connection_pool():
                return {"available": 1}

            @pytest.fixture
            def connection(connection_pool):
                if connection_pool["available"] <= 0:
                    pytest.fail("connection pool exhausted by a previous run in this session")
                connection_pool["available"] -= 1
                yield "conn"
                # BUG: never released back to the pool.

            def test_c_leaks_connection(connection):
                assert connection == "conn"

            def test_d_cannot_acquire(connection):
                assert connection == "conn"
        """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
            out_of_session_retries_enabled(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s", "-p", "no:randomly")

        assert result.ret == 1
        assert_stats(result, passed=1, failed=1)

        assert len(list(event_capture.events_by_type("test_session_end"))) == 2

        victim_events = list(event_capture.events_by_test_name("test_d_cannot_acquire"))
        assert len(victim_events) == 2
        assert victim_events[0]["content"]["meta"].get("test.status") == "fail"
        assert victim_events[1]["content"]["meta"].get("test.status") == "pass"
        assert victim_events[1]["content"]["meta"].get("test.retry_reason") == "out_of_session"

    def test_disabled_by_env_var(self, pytester: Pytester) -> None:
        """The kill switch disables out-of-session retries: no second session is created."""
        pytester.makepyfile(
            test_foo="""
            def test_fail():
                assert False
        """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),  # leaves OSR disabled
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 1
        assert_stats(result, failed=1)

        assert len(list(event_capture.events_by_type("test_session_end"))) == 1
        assert len(list(event_capture.events_by_test_name("test_fail"))) == 1
