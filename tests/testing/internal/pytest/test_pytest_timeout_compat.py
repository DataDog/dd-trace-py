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

        # test_new is not in known_tests, so EFD retries it 10 times.
        known_tests: set[TestRef] = set()

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
