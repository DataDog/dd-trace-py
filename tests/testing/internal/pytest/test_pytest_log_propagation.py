"""Regression tests for ddtrace logger propagation during pytest sessions.

When a user configures a custom root logger via logging.config.dictConfig with
disable_existing_loggers: False and a StreamHandler pointing to ext://sys.stdout,
ddtrace log records emitted at interpreter shutdown (via Tracer._atexit) propagate to
the root logger's handler.  By that point pytest has already closed its captured
sys.stdout, so the handler raises ValueError: I/O operation on closed file.

Teardown protects closed stream destinations while preserving delivery to healthy
handlers and leaving user-configured propagation unchanged.

These tests use runpytest_subprocess because the bug only manifests at interpreter
shutdown (atexit), which does not fire in inline_run.
"""

from __future__ import annotations

import textwrap

from _pytest.pytester import Pytester
from _pytest.pytester import RunResult
import pytest


# ---------------------------------------------------------------------------
# Test file content strings.
# ---------------------------------------------------------------------------

# A conftest.py that installs a custom root logger handler pointing to sys.stdout,
# replicating the user's dictConfig with disable_existing_loggers: False.
# The fixture runs dictConfig during test setup so the handler is active when
# the test session finishes and the tracer's _atexit fires.
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
# subprocess stderr inspection (no --- Logging error ---).
_TEST_FAIL = textwrap.dedent(
    """\
    def test_dummy(configure_root_logger):
        assert 1 == 2
    """
)

# Infrastructure mock plugin — loaded via -p dd_log_prop_infra.
# Sets up mocks in the subprocess so the plugin can initialise without a real agent.
# Mirrors the approach in test_pytest_log_correlation.py.
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
    """Assert that no --- Logging error --- traceback appears in stderr."""
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
    auth failure.  This mirrors the approach in test_pytest_log_correlation.py.
    """
    monkeypatch.delenv("_DD_CIVISIBILITY_USE_CI_CONTEXT_PROVIDER", raising=False)
    monkeypatch.delenv("_CI_DD_API_KEY", raising=False)
    monkeypatch.setenv("DD_CIVISIBILITY_AGENTLESS_ENABLED", "true")
    monkeypatch.setenv("DD_API_KEY", "test-key")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDdtraceLoggerPropagation:
    """Verify shutdown safety without overriding the user's logging configuration."""

    def test_no_logging_error_without_ddtrace_flag(self, pytester: Pytester) -> None:
        """Without --ddtrace, the plugin still loads and must prevent the logging error.

        The tracer is initialised on import (before CLI parsing), so its _atexit
        handler fires regardless of whether --ddtrace is passed.  The fix must
        therefore run before the _is_enabled_early guard.
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
        """With --ddtrace, the logging error must also not appear."""
        pytester.makeconftest(_CONFTEST_WITH_ROOT_STREAM_HANDLER)
        pytester.makepyfile(dd_log_prop_infra=_INFRA_PLUGIN)
        pytester.makepyfile(test_file=_TEST_FAIL)

        result = pytester.runpytest_subprocess("--ddtrace", "-p", "dd_log_prop_infra", "-v")

        result.assert_outcomes(failed=1)
        _assert_no_logging_error(result)

    @pytest.mark.parametrize("propagate", [False, True])
    def test_ddtrace_logger_propagation_is_preserved(
        self, pytester: Pytester, logging_probe_env: None, propagate: bool
    ) -> None:
        pytester.makepyfile(early_logging=f"import logging\nlogging.getLogger('ddtrace').propagate = {propagate!r}")
        pytester.makepyfile(
            test_file=f"""\
            import logging

            def test_propagation():
                assert logging.getLogger("ddtrace").propagate is {propagate!r}
            """
        )
        result = _run_logging_probe(pytester, "-p", "early_logging")
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


@pytest.fixture()
def logging_probe_env(pytester: Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the nested session independent of the repository's logging configuration."""
    for name in (
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
        "PYTEST_XDIST_WORKER",
        "PYTEST_XDIST_WORKER_COUNT",
        "DD_TRACE_DEBUG",
        "DD_TRACE_LOG_LEVEL",
        "DD_TRACE_LOG_FILE",
        "DD_TRACE_LOG_FILE_LEVEL",
        "DD_TRACE_LOG_FILE_SIZE_BYTES",
        "DD_CIVISIBILITY_LOG_LEVEL",
        "DD_LOGS_INJECTION",
        "DD_AGENTLESS_LOG_SUBMISSION_ENABLED",
        "_DD_CIVISIBILITY_USE_CI_CONTEXT_PROVIDER",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.setenv("DD_PYTEST_USE_NEW_PLUGIN", "true")
    monkeypatch.setenv("DD_CIVISIBILITY_ENABLED", "true")
    monkeypatch.setenv("DD_TRACE_ENABLED", "true")
    monkeypatch.setenv("DD_TRACE_LOG_STREAM_HANDLER", "true")
    monkeypatch.setenv("DD_INSTRUMENTATION_TELEMETRY_ENABLED", "false")
    monkeypatch.setenv("DD_REMOTE_CONFIGURATION_ENABLED", "false")
    pytester.makeini("[pytest]")


def _run_logging_probe(pytester: Pytester, *args: str) -> RunResult:
    # AIDEV-NOTE: Use stock pytest in a subprocess. tests/conftest.py overrides caplog
    # to restore ddtrace propagation, which would hide the compatibility regression.
    return pytester.runpytest_subprocess(
        "-p",
        "ddtrace.testing.internal.pytest.entry_point",
        "--confcutdir",
        str(pytester.path),
        "-v",
        *args,
    )


_DELIVERY_PROBE = """\
import logging
from unittest.mock import Mock

import pytest

import ddtrace
from ddtrace.contrib.internal.logging.patch import patch
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.testing.internal.logs import LogsHandler
from ddtrace.trace import Span


@pytest.fixture
def logger():
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    return logger


def test_caplog(logger, caplog):
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR):
            logger.log(level, "delivery probe %s", level)
    records = [record for record in caplog.records if record.name == LOGGER_NAME]
    assert [record.levelno for record in records] == [10, 20, 30, 40]


def test_root_handler(logger):
    handler = logging.Handler()
    handler.emit = Mock()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        logger.error("root delivery probe")
        handler.emit.assert_called_once()
        assert handler.emit.call_args.args[0].getMessage() == "root delivery probe"
    finally:
        root.removeHandler(handler)


def test_submission_handler(logger):
    # Exercise the real handler's routing and encoding without sending any data.
    writer = Mock(hostname="test-host", service="test-service")
    handler = LogsHandler(writer)
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    root.addHandler(handler)
    try:
        logger.error("submission delivery probe")
        writer.put_event.assert_called_once()
        event = writer.put_event.call_args.args[0]
        assert event["message"] == "submission delivery probe"
        assert event["status"] == "error"
    finally:
        root.removeHandler(handler)


def test_direct_handler(logger):
    patch()
    handler = logging.Handler()
    handler.emit = Mock()
    logger.addHandler(handler)
    previous = ddtrace.tracer.context_provider.active()
    span = Span("correlation-probe")
    ddtrace.tracer.context_provider.activate(span)
    try:
        logger.error("direct delivery probe")
        handler.emit.assert_called_once()
        record = handler.emit.call_args.args[0]
        assert getattr(record, "dd.trace_id") == format_trace_id(span.trace_id)
        assert getattr(record, "dd.span_id") == str(span.span_id)
    finally:
        ddtrace.tracer.context_provider.activate(previous)
        logger.removeHandler(handler)
"""


@pytest.mark.usefixtures("logging_probe_env")
class TestLoggingDeliveryCompatibility:
    """Preservation probes that catch a blanket propagation cutoff.

    These intentionally assert delivery, not the implementation's propagate value.
    Application and direct-handler cases are unaffected controls.
    """

    @pytest.mark.parametrize("logger_name", ["ddtrace._trace.tracer", "application"])
    @pytest.mark.parametrize("destination", ["caplog", "root_handler", "submission_handler", "direct_handler"])
    def test_record_delivery(
        self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch, logger_name: str, destination: str
    ) -> None:
        if destination == "direct_handler":
            monkeypatch.setenv("DD_LOGS_INJECTION", "true")
        pytester.makepyfile(test_delivery=f"LOGGER_NAME = {logger_name!r}\n" + _DELIVERY_PROBE)
        result = _run_logging_probe(pytester, "-k", f"test_{destination}")
        result.assert_outcomes(passed=1)

    @pytest.mark.parametrize("logger_name", ["ddtrace._trace.tracer", "application"])
    @pytest.mark.parametrize("destination", ["live", "file", "failure_report"])
    def test_pytest_log_output(self, pytester: Pytester, logger_name: str, destination: str) -> None:
        pytester.makepyfile(
            test_output=f"""\
            import logging

            def test_output():
                logging.getLogger({logger_name!r}).error("output delivery probe")
                assert {destination != "failure_report"!r}
            """
        )
        log_format = "LOG-PROBE:%(name)s:%(message)s"
        log_file = pytester.path / "pytest.log"
        options = {
            "live": ["--log-cli-level=DEBUG", f"--log-cli-format={log_format}"],
            "file": [f"--log-file={log_file}", "--log-file-level=DEBUG", f"--log-file-format={log_format}"],
            "failure_report": ["--log-level=DEBUG", f"--log-format={log_format}"],
        }
        result = _run_logging_probe(pytester, *options[destination])
        if destination == "failure_report":
            result.assert_outcomes(failed=1)
            result.stdout.fnmatch_lines(["*Captured log call*"])
        else:
            result.assert_outcomes(passed=1)
        # The prefix proves delivery via pytest's handler, not the tracer's stderr handler
        # or a source-code excerpt in an assertion failure.
        expected = f"LOG-PROBE:{logger_name}:output delivery probe"
        output = log_file.read_text() if destination == "file" else result.stdout.str()
        assert expected in output

    @pytest.mark.parametrize("mode", ["no_flag", "no_ddtrace", "kill_switch", "debug", "ci_none", "no_stream"])
    @pytest.mark.parametrize("when", ["before_plugin", "after_plugin"])
    def test_root_only_configuration(
        self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch, mode: str, when: str
    ) -> None:
        """Root-only logging is the documented workaround for duplicate tracer logs."""
        options = []
        if mode == "no_ddtrace":
            options.append("--no-ddtrace")
        elif mode == "kill_switch":
            monkeypatch.setenv("DD_CIVISIBILITY_ENABLED", "false")
            options.append("--ddtrace")
        elif mode == "debug":
            monkeypatch.setenv("DD_TRACE_DEBUG", "true")
        elif mode == "ci_none":
            monkeypatch.setenv("DD_CIVISIBILITY_LOG_LEVEL", "NONE")
        elif mode == "no_stream":
            monkeypatch.setenv("DD_TRACE_LOG_STREAM_HANDLER", "false")

        pytester.makepyfile(
            root_logging_config="""\
            import io
            import logging
            from logging.config import dictConfig

            output = io.StringIO()

            def configure():
                dictConfig({
                    "version": 1,
                    "disable_existing_loggers": False,
                    "handlers": {"probe": {"class": "logging.StreamHandler", "stream": output}},
                    "root": {"level": "DEBUG", "handlers": ["probe"]},
                })
                logger = logging.getLogger("ddtrace")
                for handler in list(logger.handlers):
                    logger.removeHandler(handler)
            """
        )
        if when == "before_plugin":
            pytester.makepyfile(
                early_logging="""\
                import logging
                from root_logging_config import configure

                configure()
                logging.getLogger("ddtrace").propagate = True
                """
            )
            # Explicit -p plugins are imported before pytest_load_initial_conftests runs.
            options.extend(["-p", "early_logging"])
        pytester.makepyfile(
            test_root_config=f"""\
            import logging
            from root_logging_config import configure, output

            def test_root_config():
                if {when == "after_plugin"!r}:
                    configure()
                logging.getLogger("application").error("application root probe")
                logging.getLogger("ddtrace._trace.tracer").error("tracer root probe")
                assert "application root probe" in output.getvalue()
                assert "tracer root probe" in output.getvalue()
            """
        )
        result = _run_logging_probe(pytester, *options)
        result.assert_outcomes(passed=1)


@pytest.mark.usefixtures("logging_probe_env")
class TestLoggingDeliveryControls:
    @pytest.mark.parametrize("capture", ["fd", "sys", "no"])
    @pytest.mark.parametrize("destination", ["root", "ddtrace", "ddtrace._trace.tracer"])
    def test_shutdown_preserves_healthy_root_handler(self, pytester: Pytester, capture: str, destination: str) -> None:
        """A dead destination must not suppress a record at a healthy root handler."""
        pytester.makepyfile(
            test_shutdown=f"""\
            import atexit
            import logging
            import sys

            def test_shutdown():
                root = logging.getLogger()
                root.setLevel(logging.DEBUG)
                root.addHandler(logging.FileHandler("root.log"))
                destination = root if {destination!r} == "root" else logging.getLogger({destination!r})
                destination.addHandler(logging.StreamHandler(sys.stdout))
                logger = logging.getLogger("ddtrace._trace.tracer")
                logger.error("during test delivery")
                atexit.register(logger.error, "shutdown delivery")
            """
        )
        result = _run_logging_probe(pytester, f"--capture={capture}")
        result.assert_outcomes(passed=1)
        output = (pytester.path / "root.log").read_text()
        assert "during test delivery" in output
        assert "shutdown delivery" in output
        _assert_no_logging_error(result)

    def test_handler_installed_during_unconfigure(self, pytester: Pytester) -> None:
        pytester.makeconftest(
            """\
            import atexit
            import io
            import logging

            def pytest_unconfigure(config):
                stream = io.StringIO()
                root = logging.getLogger()
                root.addHandler(logging.StreamHandler(stream))
                root.addHandler(logging.FileHandler("late.log"))
                config.add_cleanup(stream.close)
                atexit.register(logging.getLogger("ddtrace._trace.tracer").error, "late shutdown delivery")
            """
        )
        pytester.makepyfile("def test_pass(): pass")
        result = _run_logging_probe(pytester)
        result.assert_outcomes(passed=1)
        assert "late shutdown delivery" in (pytester.path / "late.log").read_text()
        _assert_no_logging_error(result)

    def test_collection_error_still_protects_shutdown(self, pytester: Pytester) -> None:
        pytester.makeconftest(
            """\
            import atexit
            import logging
            import sys

            root = logging.getLogger()
            root.addHandler(logging.StreamHandler(sys.stdout))
            root.addHandler(logging.FileHandler("collection.log"))
            atexit.register(logging.getLogger("ddtrace._trace.tracer").error, "collection shutdown delivery")
            """
        )
        pytester.makepyfile("raise RuntimeError('collection failure')")
        result = _run_logging_probe(pytester)
        result.assert_outcomes(errors=1)
        assert "collection shutdown delivery" in (pytester.path / "collection.log").read_text()
        _assert_no_logging_error(result)

    def test_repeated_pytest_main_preserves_logging_and_tracer(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            test_session="""\
            import logging
            import sys

            def test_session():
                logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))
                logging.getLogger("ddtrace._trace.tracer").error("during session")
            """
        )
        script = pytester.makepyfile(
            run_sessions="""\
            import logging
            import pytest
            import ddtrace
            from ddtrace.testing.internal.logging import _DDTraceClosedStreamFilter

            root = logging.getLogger()
            root.setLevel(logging.DEBUG)
            root.addHandler(logging.FileHandler("sessions.log"))
            for _ in range(2):
                assert pytest.main([
                    "-p", "ddtrace.testing.internal.pytest.entry_point",
                    "--confcutdir=.", "-q", "test_session.py",
                ]) == 0
                assert ddtrace.tracer.enabled
                assert logging.getLogger("ddtrace").propagate is True
                for handler in root.handlers:
                    if type(handler) is logging.StreamHandler:
                        assert sum(isinstance(f, _DDTraceClosedStreamFilter) for f in handler.filters) == 1
                logging.getLogger("ddtrace._trace.tracer").error("after session")
            """
        )
        result = pytester.runpython(script)
        assert result.ret == 0
        output = (pytester.path / "sessions.log").read_text()
        assert output.count("during session") == 2
        assert output.count("after session") == 2
        _assert_no_logging_error(result)

    @pytest.mark.parametrize("debug", [False, True])
    def test_tracer_file_logging(self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch, debug: bool) -> None:
        log_file = pytester.path / "tracer.log"
        monkeypatch.setenv("DD_TRACE_LOG_FILE", str(log_file))
        monkeypatch.setenv("DD_TRACE_LOG_FILE_LEVEL", "DEBUG")
        monkeypatch.setenv("DD_TRACE_DEBUG", str(debug).lower())
        pytester.makepyfile(
            test_file="""\
            import logging

            def test_file():
                logging.getLogger("ddtrace._trace.tracer").error("tracer file probe")
            """
        )
        result = _run_logging_probe(pytester)
        result.assert_outcomes(passed=1)
        assert "tracer file probe" in log_file.read_text()
        _assert_no_logging_error(result)

    def test_shutdown_emission_reaches_direct_handler(self, pytester: Pytester) -> None:
        """Absence of a shutdown traceback must not mean no shutdown record was emitted."""
        pytester.makeconftest(_CONFTEST_WITH_ROOT_STREAM_HANDLER)
        pytester.makepyfile(
            test_shutdown="""\
            import atexit
            import logging

            def test_shutdown(configure_root_logger):
                logger = logging.getLogger("ddtrace._trace.tracer")
                logger.addHandler(logging.FileHandler("shutdown.log"))
                atexit.register(logger.error, "shutdown delivery probe")
            """
        )
        result = _run_logging_probe(pytester)
        result.assert_outcomes(passed=1)
        assert "shutdown delivery probe" in (pytester.path / "shutdown.log").read_text()
        _assert_no_logging_error(result)
