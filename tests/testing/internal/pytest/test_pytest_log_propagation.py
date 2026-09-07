"""Regression tests for ddtrace logger propagation during pytest sessions.

When a user configures a custom root logger via ``logging.config.dictConfig`` with
``disable_existing_loggers: False`` and a ``StreamHandler`` pointing to ``ext://sys.stdout``,
ddtrace log records emitted at interpreter shutdown (via ``Tracer._atexit``) propagate to
the root logger's handler.  By that point pytest has already closed its captured
``sys.stdout``, so the handler raises ``ValueError: I/O operation on closed file``.

The fix sets ``logging.getLogger("ddtrace").propagate = False`` in
``pytest_load_initial_conftests`` (before the ``--ddtrace`` early-enable guard), mirroring
the old plugin's unconditional ``take_over_logger_stream_handler()`` call.

These tests use ``runpytest_subprocess`` because the bug only manifests at interpreter
shutdown (``atexit``), which does not fire in ``inline_run``.
"""

from __future__ import annotations

import textwrap

from _pytest.pytester import Pytester
import pytest


# ---------------------------------------------------------------------------
# Test file content strings.
# ---------------------------------------------------------------------------

# A conftest.py that installs a custom root logger handler pointing to sys.stdout,
# replicating the user's ``dictConfig`` with ``disable_existing_loggers: False``.
# The fixture runs ``dictConfig`` during test setup so the handler is active when
# the test session finishes and the tracer's ``_atexit`` fires.
_CONFTEST_WITH_ROOT_STREAM_HANDLER = textwrap.dedent(
    """\
    from logging.config import dictConfig
    import pytest


    @pytest.fixture
    def configure_root_logger():
        dictConfig({
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "simple": {
                    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                },
            },
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "level": "DEBUG",
                    "formatter": "simple",
                    "stream": "ext://sys.stdout",
                },
            },
            "loggers": {
                "root": {
                    "level": "DEBUG",
                    "handlers": ["stdout"],
                },
            },
        })
    """
)

# A simple failing test that triggers the fixture.  We use a failing assertion so
# the test session has a non-trivial exit code, but the key assertion is in the
# subprocess stderr inspection (no ``--- Logging error ---``).
_TEST_FAIL = textwrap.dedent(
    """\
    def test_dummy(configure_root_logger):
        assert 1 == 2
    """
)

# A passing test that verifies the ddtrace logger's propagate attribute is False
# at test-run time (i.e. after ``pytest_load_initial_conftests`` has run).
_TEST_PROPAGATE_IS_FALSE = textwrap.dedent(
    """\
    import logging


    def test_ddtrace_logger_propagate_is_false():
        ddtrace_logger = logging.getLogger("ddtrace")
        assert ddtrace_logger.propagate is False, (
            f"ddtrace logger propagate should be False, got {ddtrace_logger.propagate}"
        )
    """
)


# Infrastructure mock plugin — loaded via ``-p dd_log_prop_infra``.
# Sets up mocks in the subprocess so the plugin can initialise without a real agent.
# Mirrors the approach in ``test_pytest_log_correlation.py``.
_INFRA_PLUGIN = textwrap.dedent(
    """\
    from unittest.mock import Mock


    def pytest_configure(config):
        from ddtrace.testing.internal.pytest.plugin import SESSION_MANAGER_STASH_KEY, _stash_get

        session_manager = _stash_get(config, SESSION_MANAGER_STASH_KEY, None)
        if session_manager is not None:
            session_manager.writer = Mock()
            session_manager.coverage_writer = Mock()
    """
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_no_logging_error(result) -> None:
    """Assert that no ``--- Logging error ---`` traceback appears in stderr."""
    stderr = "\n".join(result.errlines)
    assert "--- Logging error ---" not in stderr, f"Expected no logging error in stderr, but found one:\n{stderr}"
    assert "I/O operation on closed file" not in stderr, (
        f"Expected no 'I/O operation on closed file' in stderr:\n{stderr}"
    )


@pytest.fixture()
def subprocess_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate pytester subprocesses from the outer CI environment.

    Force agentless mode with a fake API key so that SessionManager.detect_setup()
    succeeds without network calls and get_settings() falls back to defaults on
    auth failure.  This mirrors the approach in ``test_pytest_log_correlation.py``.
    """
    monkeypatch.delenv("_DD_CIVISIBILITY_USE_CI_CONTEXT_PROVIDER", raising=False)
    monkeypatch.delenv("_CI_DD_API_KEY", raising=False)
    monkeypatch.setenv("DD_CIVISIBILITY_AGENTLESS_ENABLED", "true")
    monkeypatch.setenv("DD_API_KEY", "test-key")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDdtraceLoggerPropagation:
    """Verify that the ddtrace logger does not propagate to user-configured root handlers."""

    def test_no_logging_error_without_ddtrace_flag(self, pytester: Pytester) -> None:
        """Without ``--ddtrace``, the plugin still loads and must prevent the logging error.

        The tracer is initialised on import (before CLI parsing), so its ``_atexit``
        handler fires regardless of whether ``--ddtrace`` is passed.  The fix must
        therefore run before the ``_is_enabled_early`` guard.
        """
        pytester.makeconftest(_CONFTEST_WITH_ROOT_STREAM_HANDLER)
        pytester.makepyfile(test_file=_TEST_FAIL)

        # Do NOT pass -s: the bug only manifests when pytest's stdout capture is
        # active (the default).  With -s, sys.stdout is never replaced and the
        # StreamHandler never sees a closed file.
        result = pytester.runpytest_subprocess("-v")

        # The test should fail (assert 1 == 2)
        result.assert_outcomes(failed=1)

        # The key assertion: no "Logging error" or "I/O operation on closed file"
        # should appear in stderr.
        _assert_no_logging_error(result)

    def test_no_logging_error_with_ddtrace_flag(
        self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch, subprocess_env: None
    ) -> None:
        """With ``--ddtrace``, the logging error must also not appear."""
        pytester.makeconftest(_CONFTEST_WITH_ROOT_STREAM_HANDLER)
        pytester.makepyfile(dd_log_prop_infra=_INFRA_PLUGIN)
        pytester.makepyfile(test_file=_TEST_FAIL)

        result = pytester.runpytest_subprocess("--ddtrace", "-p", "dd_log_prop_infra", "-v")

        result.assert_outcomes(failed=1)
        _assert_no_logging_error(result)

    def test_ddtrace_logger_propagate_is_false(self, pytester: Pytester) -> None:
        """The ddtrace logger must have ``propagate = False`` during the test session.

        This holds even without ``--ddtrace`` because the fix runs unconditionally
        in ``pytest_load_initial_conftests``.
        """
        pytester.makepyfile(test_file=_TEST_PROPAGATE_IS_FALSE)

        result = pytester.runpytest_subprocess("-v")
        result.assert_outcomes(passed=1)

    def test_no_logging_error_without_custom_logger(self, pytester: Pytester) -> None:
        """Without a custom root logger, there should be no logging error either.

        This is a sanity check to ensure the fix does not introduce regressions
        in the common case where no custom logging is configured.
        """
        pytester.makepyfile(test_file="def test_pass(): assert True")

        result = pytester.runpytest_subprocess("-v")
        result.assert_outcomes(passed=1)
        _assert_no_logging_error(result)
