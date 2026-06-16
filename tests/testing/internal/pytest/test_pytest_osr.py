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


def _osr_events(event_capture: EventCapture) -> list[t.Any]:
    return [
        event
        for event in event_capture.events_by_type("test")
        if event["content"]["meta"].get("test.retry_reason") == "out_of_session"
    ]


class TestOutOfSessionRetries:
    def test_atr_exhausted_then_rerun_out_of_session(self, pytester: Pytester) -> None:
        """A test that fails every ATR retry is re-run once, in a new session, tagged retry_reason=out_of_session."""
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
                result = pytester.inline_run("--ddtrace", "-v", "-s", "-p", "no:randomly")

        # OSR does not change pytest's verdict: the original failure stands.
        assert result.ret == 1
        assert_stats(result, failed=1)

        # Two sessions: the original one and the out-of-session retry one.
        assert len(list(event_capture.events_by_type("test_session_end"))) == 2

        test_events = list(event_capture.events_by_test_name("test_fail"))
        # 1 initial + 5 ATR retries (session 1) + 1 out-of-session retry (session 2).
        assert len(test_events) == 7

        atr_events = [e for e in test_events if e["content"]["meta"].get("test.retry_reason") == "auto_test_retry"]
        osr_events = [e for e in test_events if e["content"]["meta"].get("test.retry_reason") == "out_of_session"]
        assert len(atr_events) == 5
        assert len(osr_events) == 1
        assert osr_events[0]["content"]["meta"].get("test.is_retry") == "true"

    def test_plain_failure_without_atr_is_not_retried(self, pytester: Pytester) -> None:
        """With ATR disabled there is nothing to exhaust, so OSR does not run (no second session)."""
        pytester.makepyfile(
            test_foo="""
            def test_fail():
                assert False
        """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),  # ATR disabled
            ),
            setup_standard_mocks(),
            out_of_session_retries_enabled(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s", "-p", "no:randomly")

        assert result.ret == 1
        assert_stats(result, failed=1)

        assert len(list(event_capture.events_by_type("test_session_end"))) == 1
        assert len(list(event_capture.events_by_test_name("test_fail"))) == 1
        assert _osr_events(event_capture) == []

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
                return_value=mock_api_client_settings(auto_retries_enabled=True),
            ),
            setup_standard_mocks(),
            out_of_session_retries_enabled(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s", "-p", "no:randomly")

        assert result.ret == 0
        assert_stats(result, passed=1)

        assert len(list(event_capture.events_by_type("test_session_end"))) == 1
        assert _osr_events(event_capture) == []

    def test_flaky_test_recovered_by_atr_is_not_rerun(self, pytester: Pytester) -> None:
        """A test that fails once but passes on an ATR retry is not exhausted, so OSR does not run."""
        pytester.makepyfile(
            test_foo="""
            class TestFlaky:
                count = 0
                def test_flaky(self):
                    TestFlaky.count += 1
                    assert TestFlaky.count > 1  # fails once, then passes on the first ATR retry
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
                result = pytester.inline_run("--ddtrace", "-v", "-s", "-p", "no:randomly")

        assert result.ret == 0

        # ATR recovered it (1 fail + 1 pass), so it is not exhausted and OSR must not run.
        assert len(list(event_capture.events_by_type("test_session_end"))) == 1
        assert _osr_events(event_capture) == []

    def test_at_most_five_tests_are_rerun(self, pytester: Pytester) -> None:
        """When more than five tests exhaust ATR, only five are picked for out-of-session retries."""
        pytester.makepyfile(test_foo="\n".join(f"def test_fail_{i}():\n    assert False\n" for i in range(7)))

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(auto_retries_enabled=True),
            ),
            setup_standard_mocks(),
            out_of_session_retries_enabled(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s", "-p", "no:randomly")

        assert result.ret == 1
        assert_stats(result, failed=7)

        assert len(list(event_capture.events_by_type("test_session_end"))) == 2
        assert len(_osr_events(event_capture)) == 5

    def test_state_leak_passes_when_retried_in_isolation(self, pytester: Pytester) -> None:
        """A test that fails every ATR retry due to a polluted session fixture passes out of session.

        This is the core motivating scenario: ATR retries reuse the polluted session-scoped fixture and all fail, then
        the out-of-session retry runs the test alone in a fresh session, so the polluting test does not run and the
        fixture is recreated clean.

        NOTE: the victim must not be the last collected test. ATR retries the last test with ``nextitem=None``, which
        tears down session-scoped fixtures between attempts — so ATR would recover the leak itself and never exhaust.
        A trailing test keeps ``nextitem`` non-None during the victim's retries, so the session fixture (and the leak)
        persists across them, ATR exhausts, and OSR takes over.
        """
        pytester.makepyfile(
            test_foo="""
            import pytest

            @pytest.fixture(scope="session")
            def shared_resource():
                return {"polluted": False}

            def test_a_pollutes(shared_resource):
                shared_resource["polluted"] = True
                assert True

            def test_b_victim(shared_resource):
                assert shared_resource["polluted"] is False

            def test_c_trailing():
                assert True
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
                result = pytester.inline_run("--ddtrace", "-v", "-s", "-p", "no:randomly")

        # The main session still failed (OSR does not change pytest's verdict).
        assert result.ret == 1

        assert len(list(event_capture.events_by_type("test_session_end"))) == 2

        victim_events = list(event_capture.events_by_test_name("test_b_victim"))
        # The final run is the out-of-session retry, and it passes on the clean slate.
        osr_runs = [e for e in victim_events if e["content"]["meta"].get("test.retry_reason") == "out_of_session"]
        assert len(osr_runs) == 1
        assert osr_runs[0]["content"]["meta"].get("test.status") == "pass"

        # The polluting test exhausts neither ATR nor OSR (it passes), so it is not re-run out of session.
        pollute_events = list(event_capture.events_by_test_name("test_a_pollutes"))
        assert all(e["content"]["meta"].get("test.retry_reason") != "out_of_session" for e in pollute_events)

    def test_disabled_by_env_var(self, pytester: Pytester) -> None:
        """The kill switch disables out-of-session retries even for ATR-exhausted tests."""
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
            setup_standard_mocks(),  # leaves OSR disabled
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s", "-p", "no:randomly")

        assert result.ret == 1
        assert_stats(result, failed=1)

        assert len(list(event_capture.events_by_type("test_session_end"))) == 1
        assert _osr_events(event_capture) == []
