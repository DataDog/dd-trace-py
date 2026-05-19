"""Tests for log correlation and agentless log submission in the ddtrace.testing pytest plugin.

Log correlation (DD_LOGS_INJECTION): log records emitted during a test carry the test's
dd.trace_id/dd.span_id, injected by the ddtrace logging patch.

Agentless log submission (DD_AGENTLESS_LOG_SUBMISSION_ENABLED): log records are forwarded to
the Datadog logs intake, also enriched with trace/span IDs via the logging patch.

Uses subprocess runs so env vars are picked up by ddtrace during initialisation.
"""

from __future__ import annotations

from _pytest.pytester import Pytester
import pytest


@pytest.fixture()
def subprocess_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate pytester subprocesses from the outer CI environment.

    pytester.runpytest_subprocess() inherits the full parent environment, so the inner
    subprocess picks up CI env vars that interfere with the test:

    - _DD_CIVISIBILITY_USE_CI_CONTEXT_PROVIDER: disables the trace filter and log submission.
    - Agent URL / EVP proxy vars: cause SessionManager.detect_setup() to attempt (and fail)
      a connection to an unreachable agent.  Force agentless mode with a fake API key instead,
      so detect_setup() succeeds without network calls and get_settings() falls back to
      defaults on auth failure.

    Note: agentless mode only affects how the backend *connector* is created (no network call
    during init).  The plugin features under test — log injection, LogsHandler installation —
    are connector-agnostic and behave identically in both modes.
    """
    monkeypatch.delenv("_DD_CIVISIBILITY_USE_CI_CONTEXT_PROVIDER", raising=False)
    monkeypatch.delenv("_CI_DD_API_KEY", raising=False)
    monkeypatch.setenv("DD_CIVISIBILITY_AGENTLESS_ENABLED", "true")
    monkeypatch.setenv("DD_API_KEY", "test-key")


# Infrastructure mock plugin — loaded via "-p dd_log_corr_infra".
#
# The subprocess_env fixture sets DD_CIVISIBILITY_AGENTLESS_ENABLED=true and
# DD_API_KEY=test-key so that SessionManager.detect_setup() succeeds without network
# calls (agentless mode) and get_settings() falls back to defaults on auth failure.
#
# This plugin's pytest_configure then replaces the writer *instances* on the already-created
# SessionManager with mocks, preventing any event data from being sent to Datadog.
_INFRA_PLUGIN = """\
from unittest.mock import Mock


def pytest_configure(config):
    # Replace the already-created writer instances on the SessionManager with mocks.
    # The real writers are constructed during pytest_load_initial_conftests (before this
    # hook fires), but they only start background threads and make HTTP calls when
    # start() is called in pytest_sessionstart (after this hook).  Swapping them here
    # prevents any network I/O and CI Visibility data leakage.
    from ddtrace.testing.internal.pytest.plugin import SESSION_MANAGER_STASH_KEY

    session_manager = config.stash.get(SESSION_MANAGER_STASH_KEY, None)
    if session_manager is not None:
        session_manager.writer = Mock()
        session_manager.coverage_writer = Mock()
"""


# Variant of _INFRA_PLUGIN that makes the ddtrace logging patch unimportable,
# simulating an environment where the contrib logging module is missing.
_INFRA_PLUGIN_NO_LOGGING_PATCH = """\
import sys
from unittest.mock import Mock


def pytest_configure(config):
    # Setting a module to None in sys.modules causes ImportError on subsequent imports.
    sys.modules["ddtrace.contrib.internal.logging.patch"] = None

    from ddtrace.testing.internal.pytest.plugin import SESSION_MANAGER_STASH_KEY

    session_manager = config.stash.get(SESSION_MANAGER_STASH_KEY, None)
    if session_manager is not None:
        session_manager.writer = Mock()
        session_manager.coverage_writer = Mock()
"""


# ---------------------------------------------------------------------------
# Test file content strings — each asserts directly so a passing subprocess
# (exit 0) proves the feature works.
# ---------------------------------------------------------------------------

_TEST_WITH_LOG_CORRELATION = """\
import logging
import ddtrace
from ddtrace.internal.utils.formats import format_trace_id


class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_log_correlation():
    handler = _CaptureHandler()
    logger = logging.getLogger("dd_log_corr_test")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    logger.warning("correlation check")

    assert len(handler.records) == 1, f"Expected 1 record, got {len(handler.records)}"
    record = handler.records[0]

    assert hasattr(record, "dd.trace_id"), (
        f"dd.trace_id missing from log record. "
        f"Is DD_LOGS_INJECTION set? Record keys: {sorted(record.__dict__)}"
    )
    assert hasattr(record, "dd.span_id"), "dd.span_id missing from log record"

    active = ddtrace.tracer.context_provider.active()
    assert active is not None, "No active ddtrace span during test"

    assert getattr(record, "dd.span_id") == str(active.span_id), (
        f"span_id mismatch: log has {getattr(record, 'dd.span_id')!r}, "
        f"active span has {active.span_id}"
    )
    assert getattr(record, "dd.trace_id") == format_trace_id(active.trace_id), (
        f"trace_id mismatch: log has {getattr(record, 'dd.trace_id')!r}, "
        f"active span has {active.trace_id}"
    )
"""

_TEST_WITHOUT_LOG_INJECTION = """\
import logging


class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_no_trace_ids_without_logs_injection():
    handler = _CaptureHandler()
    logger = logging.getLogger("dd_no_injection_test")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    logger.warning("no injection")

    assert len(handler.records) == 1
    record = handler.records[0]
    assert not hasattr(record, "dd.trace_id"), (
        "dd.trace_id should not be injected when DD_LOGS_INJECTION is not set"
    )
    assert not hasattr(record, "dd.span_id"), (
        "dd.span_id should not be injected when DD_LOGS_INJECTION is not set"
    )
"""

_TEST_AGENTLESS_HANDLER_INSTALLED = """\
import logging
import ddtrace
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.testing.internal.logs import LogsHandler


def test_agentless_handler_installed_and_forwards():
    root_handlers = logging.getLogger().handlers
    logs_handlers = [h for h in root_handlers if isinstance(h, LogsHandler)]
    assert len(logs_handlers) == 1, (
        f"Expected 1 LogsHandler on root logger, got {len(logs_handlers)}: {root_handlers}"
    )

    captured = []
    logs_handlers[0]._writer.put_event = lambda e: captured.append(e)

    logger = logging.getLogger("agentless_test")
    logger.warning("agentless check")

    assert len(captured) == 1, f"Expected 1 forwarded event, got {len(captured)}"
    event = captured[0]

    active = ddtrace.tracer.context_provider.active()
    assert active is not None, "No active ddtrace span"

    assert event["dd.trace_id"] == format_trace_id(active.trace_id), (
        f"trace_id mismatch: event has {event['dd.trace_id']!r}, active span has {active.trace_id}"
    )
    assert event["dd.span_id"] == str(active.span_id), (
        f"span_id mismatch: event has {event['dd.span_id']!r}, active span has {active.span_id}"
    )
    assert event["message"] == "agentless check"
    assert event["status"] == "warning"
"""

_TEST_NO_HANDLER_WITHOUT_FLAG = """\
import logging
from ddtrace.testing.internal.logs import LogsHandler


def test_no_handler_without_submission_flag():
    logs_handlers = [h for h in logging.getLogger().handlers if isinstance(h, LogsHandler)]
    assert len(logs_handlers) == 0, (
        "LogsHandler should not be installed when DD_AGENTLESS_LOG_SUBMISSION_ENABLED is not set"
    )
"""

_TEST_ROOT_LEVEL_FILTERING = """\
import logging
from ddtrace.testing.internal.logs import LogsHandler


def test_root_level_filters_child_logger_records():
    logging.root.setLevel(logging.ERROR)

    logs_handlers = [h for h in logging.getLogger().handlers if isinstance(h, LogsHandler)]
    assert len(logs_handlers) == 1

    captured = []
    logs_handlers[0]._writer.put_event = lambda e: captured.append(e)

    # Child logger with an explicit level below root's ERROR. Python's propagation passes its
    # records directly to root's handlers WITHOUT re-checking root's level — our LogsHandler.emit
    # must enforce root's level itself.
    child = logging.getLogger("root_level_test")
    child.setLevel(logging.WARNING)

    child.warning("should be filtered")
    child.error("should be forwarded")

    assert len(captured) == 1, f"Expected 1 forwarded event (ERROR only), got {len(captured)}"
    assert captured[0]["message"] == "should be forwarded"
    assert captured[0]["status"] == "error"
"""


class TestLogCorrelation:
    def test_log_records_carry_test_span_ids(
        self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch, subprocess_env: None
    ) -> None:
        """Logs emitted during a test must have dd.trace_id/dd.span_id matching the active test span."""
        monkeypatch.setenv("DD_LOGS_INJECTION", "true")

        pytester.makepyfile(dd_log_corr_infra=_INFRA_PLUGIN)
        pytester.makepyfile(test_file=_TEST_WITH_LOG_CORRELATION)

        result = pytester.runpytest_subprocess("--ddtrace", "-p", "dd_log_corr_infra", "-v", "-s")
        result.assert_outcomes(passed=1)

    def test_logging_patch_import_error_does_not_crash_session(
        self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch, subprocess_env: None
    ) -> None:
        """If the ddtrace logging patch module cannot be imported, the session must not crash.

        A warning is emitted and tests continue to run normally.
        """
        monkeypatch.setenv("DD_LOGS_INJECTION", "true")

        pytester.makepyfile(dd_log_corr_infra=_INFRA_PLUGIN_NO_LOGGING_PATCH)
        pytester.makepyfile(test_file="def test_pass(): pass")

        result = pytester.runpytest_subprocess("--ddtrace", "-p", "dd_log_corr_infra", "-v", "-s")
        result.assert_outcomes(passed=1)

    def test_without_logs_injection_no_ids_injected(
        self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch, subprocess_env: None
    ) -> None:
        """Without DD_LOGS_INJECTION, log records must not have dd.trace_id/dd.span_id."""
        monkeypatch.setenv("DD_LOGS_INJECTION", "false")

        pytester.makepyfile(dd_log_corr_infra=_INFRA_PLUGIN)
        pytester.makepyfile(test_file=_TEST_WITHOUT_LOG_INJECTION)

        result = pytester.runpytest_subprocess("--ddtrace", "-p", "dd_log_corr_infra", "-v", "-s")
        result.assert_outcomes(passed=1)


class TestAgentlessLogSubmission:
    def test_handler_installed_and_forwards_logs(
        self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch, subprocess_env: None
    ) -> None:
        """LogsHandler must be installed and forward events with correct trace IDs when both agentless flags are set."""
        monkeypatch.setenv("DD_AGENTLESS_LOG_SUBMISSION_ENABLED", "true")
        monkeypatch.setenv("DD_CIVISIBILITY_AGENTLESS_ENABLED", "true")

        pytester.makepyfile(dd_log_corr_infra=_INFRA_PLUGIN)
        pytester.makepyfile(test_file=_TEST_AGENTLESS_HANDLER_INSTALLED)

        result = pytester.runpytest_subprocess("--ddtrace", "-p", "dd_log_corr_infra", "-v", "-s")
        result.assert_outcomes(passed=1)

    def test_disabled_without_log_submission_flag(
        self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch, subprocess_env: None
    ) -> None:
        """LogsHandler must not be installed when DD_AGENTLESS_LOG_SUBMISSION_ENABLED is not set,
        even if DD_CIVISIBILITY_AGENTLESS_ENABLED is true.
        """
        monkeypatch.delenv("DD_AGENTLESS_LOG_SUBMISSION_ENABLED", raising=False)
        pytester.makepyfile(dd_log_corr_infra=_INFRA_PLUGIN)
        pytester.makepyfile(test_file=_TEST_NO_HANDLER_WITHOUT_FLAG)

        result = pytester.runpytest_subprocess("--ddtrace", "-p", "dd_log_corr_infra", "-v", "-s")
        result.assert_outcomes(passed=1)

    def test_no_handler_without_flag(self, pytester: Pytester, subprocess_env: None) -> None:
        """Without DD_AGENTLESS_LOG_SUBMISSION_ENABLED or DD_LOGS_INJECTION, LogsHandler must not be installed."""
        pytester.makepyfile(dd_log_corr_infra=_INFRA_PLUGIN)
        pytester.makepyfile(test_file=_TEST_NO_HANDLER_WITHOUT_FLAG)

        result = pytester.runpytest_subprocess("--ddtrace", "-p", "dd_log_corr_infra", "-v", "-s")
        result.assert_outcomes(passed=1)

    def test_handler_installed_via_logs_injection(
        self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch, subprocess_env: None
    ) -> None:
        """LogsHandler must be installed when DD_LOGS_INJECTION=true.

        The subprocess_env fixture forces agentless mode for CI reliability (no reachable agent
        needed), but the feature under test — LogsHandler installation via DD_LOGS_INJECTION —
        is connector-agnostic.
        """
        monkeypatch.setenv("DD_LOGS_INJECTION", "true")

        pytester.makepyfile(dd_log_corr_infra=_INFRA_PLUGIN)
        pytester.makepyfile(test_file=_TEST_AGENTLESS_HANDLER_INSTALLED)

        result = pytester.runpytest_subprocess("--ddtrace", "-p", "dd_log_corr_infra", "-v", "-s")
        result.assert_outcomes(passed=1)

    def test_no_handler_in_ci_context_provider_mode(
        self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch, subprocess_env: None
    ) -> None:
        """DD_LOGS_INJECTION=true must not install LogsHandler when _DD_CIVISIBILITY_USE_CI_CONTEXT_PROVIDER=1.

        That flag enables a separate CIVisibilityTracer with its own CIContextProvider for test spans,
        independent from the global ddtrace.tracer. The ddtrace logging patch reads from the global
        tracer, so it sees no active test span and would produce zero trace/span IDs.
        """
        monkeypatch.setenv("DD_LOGS_INJECTION", "true")
        monkeypatch.setenv("_DD_CIVISIBILITY_USE_CI_CONTEXT_PROVIDER", "1")

        pytester.makepyfile(dd_log_corr_infra=_INFRA_PLUGIN)
        pytester.makepyfile(test_file=_TEST_NO_HANDLER_WITHOUT_FLAG)

        result = pytester.runpytest_subprocess("--ddtrace", "-p", "dd_log_corr_infra", "-v", "-s")
        result.assert_outcomes(passed=1)

    def test_root_level_filters_child_logger_records(
        self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch, subprocess_env: None
    ) -> None:
        """Records from a child logger set below root's level must not be forwarded to Datadog.

        Python's propagation bypasses the parent's level check, so LogsHandler.emit enforces it.
        """
        monkeypatch.setenv("DD_AGENTLESS_LOG_SUBMISSION_ENABLED", "true")
        monkeypatch.setenv("DD_CIVISIBILITY_AGENTLESS_ENABLED", "true")

        pytester.makepyfile(dd_log_corr_infra=_INFRA_PLUGIN)
        pytester.makepyfile(test_file=_TEST_ROOT_LEVEL_FILTERING)

        result = pytester.runpytest_subprocess("--ddtrace", "-p", "dd_log_corr_infra", "-v", "-s")
        result.assert_outcomes(passed=1)
