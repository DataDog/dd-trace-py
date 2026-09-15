"""Regression tests for pytest-timeout compatibility with our retry logic.

When func_only=False (the default), pytest-timeout installs its per-test timer in
its pytest_runtest_protocol hookwrapper, which fires only once for the whole item
lifecycle.  Our plugin retries tests by calling runtestprotocol() directly inside
that single hookwrapper invocation, so without an explicit reset every retry
attempt would share the same cumulative budget — causing teardowns on later
attempts to be cut short by a spurious timeout.

These tests verify that _reset_pytest_timeout is called once per attempt (so each
retry starts with a fresh budget) and that the integration with pytest-timeout is
correct end-to-end.
"""

from __future__ import annotations

import pathlib
import signal
from unittest.mock import patch

from _pytest.pytester import Pytester
import pytest


pytest_timeout = pytest.importorskip("pytest_timeout", reason="pytest-timeout not installed")

from ddtrace.testing.internal.test_data import ModuleRef  # noqa: E402
from ddtrace.testing.internal.test_data import SuiteRef  # noqa: E402  # used in TestRef constructor
from ddtrace.testing.internal.test_data import TestRef  # noqa: E402
from tests.testing.internal.pytest.utils import assert_stats  # noqa: E402
from tests.testing.mocks import mock_api_client_settings  # noqa: E402
from tests.testing.mocks import setup_standard_mocks  # noqa: E402


class TestPytestTimeoutRetryCompat:
    """Verify that each retry attempt gets its own pytest-timeout budget."""

    def test_timer_is_rearmed_for_every_attempt(self, pytester: Pytester, tmp_path: pathlib.Path) -> None:
        """timer is cancelled and re-armed once per attempt, including all retries.

        The test intercepts pytest_timeout_set_timer via a conftest plugin to count
        how many times the timer is armed during a single test item's lifecycle. With
        ATR enabled, a failing test runs 1 initial attempt + 5 retries = 6 attempts.

        Expected set_timer call count:
          1  (from pytest-timeout's own pytest_runtest_protocol hookwrapper)
        + 6  (one per _do_one_test_run call, from our _reset_pytest_timeout)
        = 7

        Without _reset_pytest_timeout the count would be just 1 (the initial arm from
        pytest-timeout's hookwrapper), because the subsequent runtestprotocol() calls
        do not re-trigger that hookwrapper.
        """
        count_file = tmp_path / "set_timer_calls.txt"
        count_file.write_text("0")

        # Conftest that intercepts the two public pytest-timeout hooks to (a) count
        # set_timer calls and (b) avoid installing a real timer so the test is not
        # sensitive to wall-clock timing.
        pytester.makeconftest(
            f"""
import pathlib
import pytest

_count_file = pathlib.Path(r"{count_file}")


@pytest.hookimpl(tryfirst=True)
def pytest_timeout_set_timer(item, settings):
    _count_file.write_text(str(int(_count_file.read_text()) + 1))
    item.cancel_timeout = lambda: None  # satisfy pytest-timeout's cancel protocol
    return True  # firstresult=True: prevents the real timer from being installed


@pytest.hookimpl(tryfirst=True)
def pytest_timeout_cancel_timer(item):
    item.cancel_timeout = None
    return True
"""
        )

        pytester.makepyfile(
            test_foo="""
            def test_always_fails():
                assert False
        """
        )

        known_tests: set[TestRef] = {
            TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_always_fails"),
        }

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    auto_retries_enabled=True,
                    known_tests_enabled=True,
                    known_tests=known_tests,
                ),
            ),
            setup_standard_mocks(),
        ):
            result = pytester.inline_run("--ddtrace", "--timeout=30", "-v", "-s")

        assert result.ret == 1
        assert_stats(result, failed=1)

        # ATR: 1 initial attempt + 5 retries = 6 attempts.
        # pytest-timeout's own hookwrapper arms the timer once at the start (count=1),
        # then _reset_pytest_timeout re-arms it once per _do_one_test_run call (count=+6).
        assert int(count_file.read_text()) == 7

    def test_efd_timer_rearmed_for_every_attempt(self, pytester: Pytester, tmp_path: pathlib.Path) -> None:
        """Same invariant holds for EFD: timer is rearmed once per attempt."""
        count_file = tmp_path / "set_timer_calls.txt"
        count_file.write_text("0")

        pytester.makeconftest(
            f"""
import pathlib
import pytest

_count_file = pathlib.Path(r"{count_file}")


@pytest.hookimpl(tryfirst=True)
def pytest_timeout_set_timer(item, settings):
    _count_file.write_text(str(int(_count_file.read_text()) + 1))
    item.cancel_timeout = lambda: None
    return True


@pytest.hookimpl(tryfirst=True)
def pytest_timeout_cancel_timer(item):
    item.cancel_timeout = None
    return True
"""
        )

        pytester.makepyfile(
            test_foo="""
            def test_new():
                assert True
        """
        )

        # known_tests must be non-empty for EFD to activate; test_new is absent so
        # EFD treats it as a new test and retries it 10 times.
        known_tests: set[TestRef] = {
            TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_known_other"),
        }

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    efd_enabled=True,
                    known_tests_enabled=True,
                    known_tests=known_tests,
                ),
            ),
            setup_standard_mocks(),
        ):
            result = pytester.inline_run("--ddtrace", "--timeout=30", "-v", "-s")

        assert result.ret == 0
        assert_stats(result, passed=1)

        # EFD: 1 initial attempt + 10 retries = 11 attempts.
        # 1 (pytest-timeout hookwrapper) + 11 (our _reset_pytest_timeout) = 12.
        assert int(count_file.read_text()) == 12


class TestPytestTimeoutThreadMethodAtr:
    """Regression tests for ``method="thread"`` compatibility with ATR/EFD retries.

    pytest-timeout's ``"thread"`` method arms a ``threading.Timer`` whose callback
    (``pytest_timeout.timeout_timer``) dumps stacks and calls ``os._exit(1)``. That
    hard-kills the process, so the first timed-out test takes the whole session down
    before Datadog Auto Test Retries (or Early Flake Detection) ever gets a chance to
    run. ``func_only=True`` only changes *where* the timer is armed
    (``pytest_runtest_call`` instead of ``pytest_runtest_protocol``); it does not
    change the ``os._exit`` behavior, so it does not help.

    Our plugin overrides the timer with a SIGALRM-based one (which raises
    ``pytest.fail``, a catchable failure) whenever retries are active, so a timed-out
    test becomes a normal failure that ATR can retry.

    Safety: these tests patch ``pytest_timeout.timeout_timer`` to a recording no-op so
    the ``os._exit`` path can never fire, even on unfixed code. That lets the tests run
    in-process (``inline_run``) without risking the test runner. The regression marker
    is whether the ``os._exit`` callback was invoked: after the fix it must *not* be
    (our SIGALRM timer replaced the thread timer), and the timed-out test must be
    retried by ATR instead of killing the process.
    """

    @staticmethod
    def _make_timeout_timer_spy_conftest(calls_file: pathlib.Path) -> str:
        return (
            "import pathlib\n"
            "import pytest_timeout\n"
            "\n"
            f"_calls = pathlib.Path(r'{calls_file}')\n"
            "\n"
            "def _spy_timeout_timer(item, settings):\n"
            "    # Record the call but do NOT call os._exit(1). This neutralizes the\n"
            "    # dangerous thread-timer path so the test is safe even on unfixed code.\n"
            "    _calls.write_text(str(int(_calls.read_text()) + 1))\n"
            "\n"
            "pytest_timeout.timeout_timer = _spy_timeout_timer\n"
        )

    def test_atr_active_thread_method_replaced_by_signal(self, pytester: Pytester, tmp_path: pathlib.Path) -> None:
        """ATR active + method="thread": the os._exit timer is not installed; ATR retries.

        Before the fix: pytest-timeout installs its threading.Timer; the patched
        timeout_timer callback fires (count > 0) but does not os._exit, so the hanging
        test runs to completion and passes -> ATR never engages -> passed=1.

        After the fix: our plugin installs a SIGALRM timer that raises pytest.fail at
        the timeout; the test fails, ATR retries it (every attempt times out) -> final
        failed=1, and the os._exit callback is never invoked -> count == 0.
        """
        if not hasattr(signal, "SIGALRM"):
            pytest.skip("SIGALRM is required for the thread->signal override")

        calls_file = tmp_path / "timeout_timer_calls.txt"
        calls_file.write_text("0")

        pytester.makeconftest(self._make_timeout_timer_spy_conftest(calls_file))

        pytester.makepyfile(
            test_foo="""
            import time
            import pytest

            @pytest.mark.timeout(0.3, method="thread", func_only=True)
            def test_hang():
                time.sleep(1)  # exceeds the 0.3s timeout
            """
        )

        known_tests: set[TestRef] = {
            TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_hang"),
        }

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    auto_retries_enabled=True,
                    known_tests_enabled=True,
                    known_tests=known_tests,
                ),
            ),
            setup_standard_mocks(),
        ):
            result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 1
        assert_stats(result, failed=1)
        # The os._exit thread-timer callback must never have been invoked: our plugin
        # replaced it with a SIGALRM timer so ATR could retry the timed-out test.
        assert int(calls_file.read_text()) == 0, (
            "expected pytest-timeout's os._exit thread timer to be replaced by a SIGALRM "
            f"timer when ATR is active, but timeout_timer was called {calls_file.read_text()} time(s)"
        )

    def test_atr_inactive_thread_method_left_untouched(self, pytester: Pytester, tmp_path: pathlib.Path) -> None:
        """ATR inactive + method="thread": we do not interfere; the thread timer is used.

        Without retries active our override is a no-op, so pytest-timeout's own thread
        timer is installed. The patched timeout_timer callback fires (count >= 1) but
        does not os._exit, so the hanging test runs to completion and passes. This
        guards against us changing behavior for customers who are not using ATR/EFD.
        """
        if not hasattr(signal, "SIGALRM"):
            pytest.skip("SIGALRM is required for the thread->signal override")

        calls_file = tmp_path / "timeout_timer_calls.txt"
        calls_file.write_text("0")

        pytester.makeconftest(self._make_timeout_timer_spy_conftest(calls_file))

        pytester.makepyfile(
            test_foo="""
            import time
            import pytest

            @pytest.mark.timeout(0.3, method="thread", func_only=True)
            def test_hang():
                time.sleep(1)
            """
        )

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
        ):
            result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0
        assert_stats(result, passed=1)
        # Without ATR we leave pytest-timeout's thread timer in place.
        assert int(calls_file.read_text()) >= 1, (
            "expected pytest-timeout's thread timer to be used when ATR is inactive, "
            f"but timeout_timer was never called (calls={calls_file.read_text()})"
        )

    def test_atr_active_thread_timeout_respects_debugger(self, pytester: Pytester, tmp_path: pathlib.Path) -> None:
        """ATR active + method="thread" + debugger active: the timeout is suppressed, not forced.

        pytest-timeout's ``timeout_sigalrm`` (the signal-method callback) honors debugger
        detection: when ``is_debugging()`` is true (e.g. a pdb session via ``SUPPRESS_TIMEOUT``,
        or a known debugger in ``sys.gettrace()``) it returns early without raising, so a test
        being debugged is not interrupted. Our override delegates to ``timeout_sigalrm`` rather
        than calling ``pytest.fail`` directly, so it inherits this behavior.

        Before the fix (direct ``pytest.fail``), the test would fail even with the debugger
        flag set. After the fix, the timeout is suppressed and the test runs to completion.
        """
        if not hasattr(signal, "SIGALRM"):
            pytest.skip("SIGALRM is required for the thread->signal override")

        calls_file = tmp_path / "timeout_timer_calls.txt"
        calls_file.write_text("0")

        pytester.makeconftest(self._make_timeout_timer_spy_conftest(calls_file))

        # Simulate an active debugger session by setting pytest-timeout's SUPPRESS_TIMEOUT
        # flag, which is_debugging() checks first. This mirrors what pytest_enter_pdb does.
        pytester.makepyfile(
            test_foo="""
            import time
            import pytest
            import pytest_timeout

            @pytest.mark.timeout(0.3, method="thread", func_only=True)
            def test_debugged():
                pytest_timeout.SUPPRESS_TIMEOUT = True  # pretend a debugger is active
                time.sleep(1)  # exceeds the 0.3s timeout, but debugger suppresses it
                pytest_timeout.SUPPRESS_TIMEOUT = False
            """
        )

        known_tests: set[TestRef] = {
            TestRef(SuiteRef(ModuleRef(""), "test_foo.py"), "test_debugged"),
        }

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    auto_retries_enabled=True,
                    known_tests_enabled=True,
                    known_tests=known_tests,
                ),
            ),
            setup_standard_mocks(),
        ):
            result = pytester.inline_run("--ddtrace", "-v", "-s")

        # The timeout was suppressed because a debugger was "active", so the test passed.
        assert result.ret == 0
        assert_stats(result, passed=1)
        # The os._exit thread-timer callback must never have been invoked.
        assert int(calls_file.read_text()) == 0, (
            "expected pytest-timeout's os._exit thread timer to be replaced by a SIGALRM "
            f"timer, but timeout_timer was called {calls_file.read_text()} time(s)"
        )
