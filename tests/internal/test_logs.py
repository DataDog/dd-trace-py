"""Tests for agentless log submission (DD_AGENTLESS_LOG_SUBMISSION_ENABLED)."""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Any
from unittest import mock

import pytest

import ddtrace
from ddtrace.internal import logs as agentless_logs
from ddtrace.internal.constants import LOG_ATTR_SPAN_ID
from ddtrace.internal.constants import LOG_ATTR_TRACE_ID
from ddtrace.internal.logs import AgentlessLogsHandler
from ddtrace.internal.logs import LogsIntakeWriter
from ddtrace.internal.logs import disable_agentless_log_submission
from ddtrace.internal.logs import enable_agentless_log_submission
from tests.utils import logs_intake_receiver


# An unreachable local URL, used wherever a writer must not reach the network.
_UNREACHABLE_URL = "http://127.0.0.1:9"


@contextlib.contextmanager
def agentless_settings(env: dict[str, str] | None = None, remove: tuple[str, ...] = ()):
    """Run the body with a patched environment and freshly resolved agentless settings."""
    from ddtrace.internal.settings._agentless import config as agentless_config
    from tests.utils import reinitialize_agentless_config

    original_state = agentless_config.__dict__
    with mock.patch.dict(os.environ, env or {}):
        for key in remove:
            os.environ.pop(key, None)
        reinitialize_agentless_config()
        try:
            yield agentless_config
        finally:
            agentless_config.__dict__ = original_state


@contextlib.contextmanager
def patched_config(**attrs: Any):
    """Temporarily override ddtrace.config attributes.

    ``mock.patch.object`` cannot restore ``ddtrace.config`` attributes: it
    deletes the attribute on exit, which the Config machinery does not support.
    """
    originals = {key: getattr(ddtrace.config, key) for key in attrs}
    for key, value in attrs.items():
        setattr(ddtrace.config, key, value)
    try:
        yield
    finally:
        for key, value in originals.items():
            setattr(ddtrace.config, key, value)


@pytest.fixture(autouse=True)
def restore_logging_state():
    """Restore the root logger and turn agentless log submission off after every test."""
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    yield
    disable_agentless_log_submission()
    root.handlers = handlers
    root.setLevel(level)


@pytest.fixture
def log_intake_receiver():
    with logs_intake_receiver() as receiver:
        yield receiver


def _make_writer(**kwargs: Any) -> LogsIntakeWriter:
    kwargs.setdefault("api_key", "test-api-key")
    kwargs.setdefault("interval", 1000.0)
    kwargs.setdefault("timeout", 1)
    kwargs.setdefault("intake_url", _UNREACHABLE_URL)
    return LogsIntakeWriter(**kwargs)


class TestLogsIntakeWriter:
    def test_intake_url(self):
        writer = _make_writer(intake_url="https://http-intake.logs.datadoghq.com")
        assert writer._url == "https://http-intake.logs.datadoghq.com/api/v2/logs"
        assert writer._endpoint == "/api/v2/logs"

        writer = _make_writer(intake_url="https://logs.example.com/base/path/")
        assert writer._url == "https://logs.example.com/base/path/api/v2/logs"
        assert writer._endpoint == "/base/path/api/v2/logs"

    def test_buffer_limit_drops_and_warns(self):
        writer = _make_writer()
        with mock.patch("ddtrace.internal.logs.logger") as mock_logger:
            for _ in range(writer._MAX_BUFFER_EVENTS + 1):
                writer.enqueue({"message": "too many"})
        mock_logger.warning.assert_called_with("log buffer full (limit is %d), dropping log", writer._MAX_BUFFER_EVENTS)
        assert len(writer._buffer) == writer._MAX_BUFFER_EVENTS

    def test_packs_split_by_event_count(self):
        writer = _make_writer()
        packs = writer._encode_packs([{"message": "m"} for _ in range(2500)])
        assert [num for _, num in packs] == [1000, 1000, 500]

    def test_packs_split_by_request_size(self):
        writer = _make_writer()
        # Each entry is ~800KB, so seven of them exceed the 5MB request limit.
        big = {"message": "x" * (800 * 1024)}
        packs = writer._encode_packs([dict(big) for _ in range(8)])
        assert len(packs) == 2
        assert sum(num for _, num in packs) == 8
        for pack, num in packs:
            assert len(pack) <= writer._MAX_REQUEST_BYTES
            assert num <= writer._MAX_EVENTS_PER_REQUEST

    def test_periodic_posts_gzipped_batches(self, log_intake_receiver):
        writer = _make_writer(intake_url=log_intake_receiver.url)
        writer.enqueue({"message": "hello", "status": "info"})
        writer.enqueue({"message": "world", "status": "error"})
        writer.periodic()

        assert len(log_intake_receiver.requests) == 1
        request = log_intake_receiver.requests[0]
        assert request.path == "/api/v2/logs"
        assert request.headers["dd-api-key"] == "test-api-key"
        assert request.headers["content-type"] == "application/json"
        assert [event["message"] for event in log_intake_receiver.events()] == ["hello", "world"]

    def test_periodic_drops_events_on_connection_error(self):
        writer = _make_writer(intake_url=_UNREACHABLE_URL)
        writer.enqueue({"message": "hello"})
        with mock.patch("ddtrace.internal.logs.logger") as mock_logger:
            writer.periodic()
        mock_logger.error.assert_called_once()
        # The batch was taken out of the buffer and dropped, not re-queued forever.
        assert writer._buffer == []

    def test_periodic_logs_error_response(self):
        writer = _make_writer()
        response = mock.Mock()
        response.status = 403
        response.read.return_value = b'{"errors": []}'
        connection = mock.Mock()
        connection.getresponse.return_value = response

        writer.enqueue({"message": "hello"})
        with (
            mock.patch("ddtrace.internal.logs.get_connection", return_value=connection),
            mock.patch("ddtrace.internal.logs.logger") as mock_logger,
        ):
            writer.periodic()

        mock_logger.error.assert_called_once_with(
            "failed to send %d logs to %r, got response code %r, status: %r",
            1,
            writer._url,
            403,
            b'{"errors": []}',
            extra={"send_to_telemetry": False},
        )
        connection.close.assert_called_once()
        assert writer._buffer == []


class TestAgentlessLogsHandler:
    @pytest.fixture
    def mock_writer(self):
        return mock.Mock(spec=LogsIntakeWriter)

    @pytest.fixture
    def handler(self, mock_writer):
        with (
            patched_config(service="test-service", env="test-env", version="1.2.3", tags={}),
            mock.patch("ddtrace.internal.logs.get_hostname", return_value="test-host"),
        ):
            handler = AgentlessLogsHandler(mock_writer)
            logging.getLogger().addHandler(handler)
            try:
                yield handler
            finally:
                logging.getLogger().removeHandler(handler)

    def _log(self, logger_name="test.app", level="warning", message="a message"):
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.DEBUG)
        getattr(logger, level)(message)

    def test_payload_shape(self, handler, mock_writer):
        self._log()
        (event,) = mock_writer.enqueue.call_args[0]
        assert event["message"] == "a message"
        assert event["ddsource"] == "python"
        assert event["service"] == "test-service"
        assert event["hostname"] == "test-host"
        assert event["status"] == "warning"
        assert event["ddtags"] == "env:test-env,version:1.2.3"
        assert isinstance(event["date"], int)
        assert event["dd.trace_id"] == "0"
        assert event["dd.span_id"] == "0"

    def test_payload_ddtags_include_dd_tags(self, mock_writer):
        with patched_config(service="s", tags={"team": "apm"}, env="", version=""):
            handler = AgentlessLogsHandler(mock_writer)
            logging.getLogger().addHandler(handler)
            try:
                self._log()
                (event,) = mock_writer.enqueue.call_args[0]
                assert event["ddtags"] == "team:apm"
            finally:
                logging.getLogger().removeHandler(handler)

    def test_payload_includes_traceback(self, handler, mock_writer):
        try:
            raise ValueError("boom")
        except ValueError:
            self._log(level="exception")
        (event,) = mock_writer.enqueue.call_args[0]
        assert event["message"].startswith("a message\nTraceback")
        assert "ValueError: boom" in event["message"]

    def test_ignores_custom_formatter(self, handler, mock_writer):
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        self._log()
        (event,) = mock_writer.enqueue.call_args[0]
        assert event["message"] == "a message"

    def test_truncates_huge_message(self, handler, mock_writer):
        logging.getLogger("test.app").setLevel(logging.DEBUG)
        logging.getLogger("test.app").warning("x" * (2 * 1024 * 1024))
        (event,) = mock_writer.enqueue.call_args[0]
        assert event["message"].endswith("... [truncated]")
        assert len(event["message"]) <= handler._MAX_MESSAGE_BYTES

    def test_uses_injected_trace_attributes(self, handler, mock_writer):
        record = logging.LogRecord("test.app", logging.WARNING, "path", 1, "m", (), None)
        record.__dict__[LOG_ATTR_TRACE_ID] = "123"
        record.__dict__[LOG_ATTR_SPAN_ID] = "456"
        handler.handle(record)
        (event,) = mock_writer.enqueue.call_args[0]
        assert event["dd.trace_id"] == "123"
        assert event["dd.span_id"] == "456"

    def test_excludes_ddtrace_loggers(self, handler, mock_writer):
        self._log(logger_name="ddtrace")
        self._log(logger_name="ddtrace.internal.something")
        assert mock_writer.enqueue.call_count == 0
        self._log(logger_name="myapp.ddtrace")
        assert mock_writer.enqueue.call_count == 1

    def test_handle_error_does_not_raise(self, handler):
        record = logging.LogRecord("test.app", logging.WARNING, "path", 1, "m", (), None)
        with mock.patch.object(handler._writer, "enqueue", side_effect=RuntimeError("nope")):
            with mock.patch.object(handler, "handleError") as handle_error:
                handler.handle(record)
            handle_error.assert_called_once()


class TestEnable:
    def _enable_env(self, **extra: str) -> dict[str, str]:
        env = {
            "DD_AGENTLESS_LOG_SUBMISSION_ENABLED": "true",
            "DD_API_KEY": "test-key",
            "DD_AGENTLESS_LOG_SUBMISSION_URL": _UNREACHABLE_URL,
        }
        env.update(extra)
        return env

    def test_enable_without_api_key_warns_and_installs_nothing(self):
        with (
            agentless_settings({"DD_AGENTLESS_LOG_SUBMISSION_ENABLED": "true"}, remove=("DD_API_KEY",)),
            mock.patch.object(ddtrace.config, "_otel_logs_enabled", False),
            mock.patch("ddtrace.internal.logs.logger") as mock_logger,
            mock.patch("ddtrace.internal.logs.LogsIntakeWriter"),
        ):
            assert not enable_agentless_log_submission()
        mock_logger.warning.assert_called_once()
        assert "DD_API_KEY" in mock_logger.warning.call_args[0][0]
        assert agentless_logs._handler is None
        assert logging.Logger.callHandlers is agentless_logs._original_call_handlers

    def test_enable_with_otel_logs_warns_and_installs_nothing(self):
        with (
            agentless_settings(self._enable_env()),
            mock.patch.object(ddtrace.config, "_otel_logs_enabled", True),
            mock.patch("ddtrace.internal.logs.logger") as mock_logger,
            mock.patch("ddtrace.internal.logs.LogsIntakeWriter"),
        ):
            assert not enable_agentless_log_submission()
        mock_logger.warning.assert_called_once()
        assert "DD_LOGS_OTEL_ENABLED" in mock_logger.warning.call_args[0][0]
        assert agentless_logs._handler is None
        assert logging.Logger.callHandlers is agentless_logs._original_call_handlers

    def test_enable_submits_records_reaching_root(self):
        with (
            agentless_settings(self._enable_env()),
            mock.patch.object(ddtrace.config, "_otel_logs_enabled", False),
            mock.patch("ddtrace.internal.logs.LogsIntakeWriter") as writer_cls,
        ):
            assert enable_agentless_log_submission()
        writer_cls.assert_called_once_with(api_key="test-key", intake_url=_UNREACHABLE_URL)
        writer = writer_cls.return_value
        writer.start.assert_called_once()
        # No handler is added, so logging.lastResort and basicConfig() keep working.
        assert not any(isinstance(h, AgentlessLogsHandler) for h in logging.getLogger().handlers)

        logging.getLogger("test.app.child").warning("submitted")
        (event,) = writer.enqueue.call_args[0]
        assert event["message"] == "submitted"

        writer.enqueue.reset_mock()
        not_propagated = logging.getLogger("test.app.no_propagate")
        not_propagated.propagate = False
        try:
            not_propagated.warning("not submitted")
        finally:
            not_propagated.propagate = True
        logging.getLogger("ddtrace.internal.something").warning("not submitted either")
        writer.enqueue.assert_not_called()

    def test_disable_stops_submission_and_discards_buffer(self):
        with (
            agentless_settings(self._enable_env()),
            mock.patch.object(ddtrace.config, "_otel_logs_enabled", False),
            mock.patch("ddtrace.internal.logs.LogsIntakeWriter") as writer_cls,
        ):
            assert enable_agentless_log_submission()
        writer = writer_cls.return_value

        disable_agentless_log_submission()

        assert logging.Logger.callHandlers is agentless_logs._original_call_handlers
        writer.reset.assert_called_once()
        writer.stop.assert_called_once()
        writer.periodic.assert_not_called()
        logging.getLogger("test.app").warning("after disable")
        writer.enqueue.assert_not_called()
        # Disabling twice is a no-op.
        disable_agentless_log_submission()

    def test_enable_defaults_to_site_intake(self):
        with (
            agentless_settings(self._enable_env(DD_SITE="datadoghq.eu"), remove=("DD_AGENTLESS_LOG_SUBMISSION_URL",)),
            mock.patch.object(ddtrace.config, "_otel_logs_enabled", False),
            mock.patch("ddtrace.internal.logs.LogsIntakeWriter") as writer_cls,
        ):
            assert enable_agentless_log_submission()
        writer_cls.assert_called_once_with(api_key="test-key", intake_url="https://http-intake.logs.datadoghq.eu")

    def test_enable_with_invalid_url_warns_and_installs_nothing(self):
        with (
            agentless_settings(self._enable_env(DD_AGENTLESS_LOG_SUBMISSION_URL="ftp://example.com")),
            mock.patch.object(ddtrace.config, "_otel_logs_enabled", False),
            mock.patch("ddtrace.internal.logs.logger") as mock_logger,
            mock.patch("ddtrace.internal.logs.LogsIntakeWriter") as writer_cls,
        ):
            assert not enable_agentless_log_submission()
        writer_cls.assert_not_called()
        mock_logger.warning.assert_called_once()
        assert agentless_logs._handler is None
        assert logging.Logger.callHandlers is agentless_logs._original_call_handlers


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_AGENTLESS_LOG_SUBMISSION_ENABLED="true", DD_API_KEY=None),
    err=lambda err: "DD_AGENTLESS_LOG_SUBMISSION_ENABLED is set but DD_API_KEY is not set" in err,
)
def test_no_api_key_warns_without_handler_in_subprocess():
    """Without DD_API_KEY, the flag produces a warning and submits nothing."""
    from ddtrace.internal import logs

    assert logs._handler is None, "log submission enabled without an API key"


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(
        DD_AGENTLESS_LOG_SUBMISSION_ENABLED="true",
        DD_LOGS_OTEL_ENABLED="true",
        DD_API_KEY="test-key",
    ),
    err=lambda err: "DD_LOGS_OTEL_ENABLED" in err,
)
def test_otel_logs_takes_precedence_in_subprocess():
    """With DD_LOGS_OTEL_ENABLED, OpenTelemetry wins: nothing is submitted, plus a startup warning."""
    from ddtrace.internal import logs

    assert logs._handler is None, "log submission enabled despite DD_LOGS_OTEL_ENABLED"


@pytest.mark.subprocess(ddtrace_run=True, env=dict(DD_AGENTLESS_LOG_SUBMISSION_ENABLED=None))
def test_not_imported_when_disabled_in_subprocess():
    """With the flag off, the log submission modules must not even be imported."""
    import sys

    assert "ddtrace.internal.logs" not in sys.modules, "log submission modules imported while disabled"


@pytest.mark.usefixtures("log_intake_receiver")
def test_submits_logs_to_intake_in_subprocess(ddtrace_run_python_code_in_subprocess, log_intake_receiver, monkeypatch):
    """Under ddtrace-run, application logs are submitted directly to the (overridden) intake URL."""
    env = os.environ.copy()
    env.update(
        {
            "DD_AGENTLESS_LOG_SUBMISSION_ENABLED": "true",
            "DD_API_KEY": "test-key",
            "DD_AGENTLESS_LOG_SUBMISSION_URL": log_intake_receiver.url,
            "DD_TRACE_AGENT_URL": log_intake_receiver.url,
            "DD_SERVICE": "agentless-logs-test",
        }
    )
    env.pop("DD_LOGS_OTEL_ENABLED", None)

    out, err, status, _ = ddtrace_run_python_code_in_subprocess(
        """
import logging

from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.trace import tracer

logger = logging.getLogger("agentless.app")

# Without any logging configuration, warnings still reach stderr through logging.lastResort.
logger.warning("standalone message")

# basicConfig() still configures the root logger.
logging.basicConfig(level=logging.INFO, format="console: %(message)s")
logger.info("info message")

with tracer.trace("submission-op") as span:
    logger.warning("correlated message")
    print("TRACE_ID=%s" % format_trace_id(span.trace_id))
""",
        env=env,
    )
    assert status == 0, err
    err_text = err.decode() if isinstance(err, bytes) else err
    assert "standalone message" in err_text, err_text
    assert "console: info message" in err_text, err_text
    assert "console: correlated message" in err_text, err_text

    events = log_intake_receiver.events()
    messages = ("standalone message", "info message", "correlated message")
    submitted = [event for event in events if event.get("message") in messages]
    assert len(submitted) == 3, events

    by_message = {event["message"]: event for event in submitted}
    assert by_message["standalone message"]["dd.trace_id"] == "0"
    assert by_message["standalone message"]["status"] == "warning"
    assert by_message["standalone message"]["ddsource"] == "python"
    assert by_message["standalone message"]["service"] == "agentless-logs-test"
    assert by_message["info message"]["status"] == "info"

    out_text = out.decode() if isinstance(out, bytes) else out
    trace_id = next(line.split("=", 1)[1] for line in out_text.splitlines() if line.startswith("TRACE_ID="))
    correlated = by_message["correlated message"]
    assert correlated["dd.trace_id"] == trace_id
    assert correlated["dd.span_id"] != "0"


@pytest.mark.usefixtures("log_intake_receiver")
def test_fork_does_not_duplicate_or_lose_logs(ddtrace_run_python_code_in_subprocess, log_intake_receiver, monkeypatch):
    """Pre-fork buffered records are submitted once (by the parent), and the child keeps submitting."""
    env = os.environ.copy()
    env.update(
        {
            "DD_AGENTLESS_LOG_SUBMISSION_ENABLED": "true",
            "DD_API_KEY": "test-key",
            "DD_AGENTLESS_LOG_SUBMISSION_URL": log_intake_receiver.url,
            "DD_TRACE_AGENT_URL": log_intake_receiver.url,
        }
    )
    env.pop("DD_LOGS_OTEL_ENABLED", None)

    _, err, status, _ = ddtrace_run_python_code_in_subprocess(
        """
import logging
import os
import sys

logger = logging.getLogger("fork.app")

# Buffered right before the fork; the flush interval is far away.
logger.warning("before fork")

pid = os.fork()
if pid == 0:
    # The child inherited the parent's buffer; it must have been cleared so
    # "before fork" is not submitted twice.
    logger.warning("from child")
    sys.exit(0)

os.waitpid(pid, 0)
logger.warning("after fork")
""",
        env=env,
    )
    assert status == 0, err

    messages = [event["message"] for event in log_intake_receiver.events()]
    assert messages.count("before fork") == 1, messages
    assert messages.count("from child") == 1, messages
    assert messages.count("after fork") == 1, messages


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires the fork start method")
@pytest.mark.usefixtures("log_intake_receiver")
def test_multiprocessing_fork_workers_submit_logs(ddtrace_run_python_code_in_subprocess, log_intake_receiver):
    """Fork-started multiprocessing workers exit through os._exit() and must still flush their logs."""
    env = os.environ.copy()
    env.update(
        {
            "DD_AGENTLESS_LOG_SUBMISSION_ENABLED": "true",
            "DD_API_KEY": "test-key",
            "DD_AGENTLESS_LOG_SUBMISSION_URL": log_intake_receiver.url,
            "DD_TRACE_AGENT_URL": log_intake_receiver.url,
        }
    )
    env.pop("DD_LOGS_OTEL_ENABLED", None)

    _, err, status, _ = ddtrace_run_python_code_in_subprocess(
        """
import logging
import multiprocessing


def work(message):
    logging.getLogger("mp.app").warning(message)


if __name__ == "__main__":
    context = multiprocessing.get_context("fork")
    process = context.Process(target=work, args=("from process",))
    process.start()
    process.join()
    # close() and join() let the worker exit normally; the context manager would terminate it.
    pool = context.Pool(1)
    pool.apply(work, ("from pool",))
    pool.close()
    pool.join()
""",
        env=env,
    )
    assert status == 0, err

    messages = [event["message"] for event in log_intake_receiver.events()]
    assert messages.count("from process") == 1, messages
    assert messages.count("from pool") == 1, messages


@pytest.mark.usefixtures("log_intake_receiver")
def test_logging_reconfiguration_keeps_submission(ddtrace_run_python_code_in_subprocess, log_intake_receiver, tmp_path):
    """basicConfig(force=True), dictConfig() and fileConfig() replace the root handlers; submission must survive."""
    config_file = tmp_path / "logging.ini"
    config_file.write_text(
        "[loggers]\nkeys=root\n\n"
        "[handlers]\nkeys=console\n\n"
        "[formatters]\nkeys=\n\n"
        "[logger_root]\nlevel=WARNING\nhandlers=console\n\n"
        "[handler_console]\nclass=StreamHandler\nargs=(sys.stderr,)\n"
    )
    env = os.environ.copy()
    env.update(
        {
            "DD_AGENTLESS_LOG_SUBMISSION_ENABLED": "true",
            "DD_API_KEY": "test-key",
            "DD_AGENTLESS_LOG_SUBMISSION_URL": log_intake_receiver.url,
            "DD_TRACE_AGENT_URL": log_intake_receiver.url,
        }
    )
    env.pop("DD_LOGS_OTEL_ENABLED", None)

    _, err, status, _ = ddtrace_run_python_code_in_subprocess(
        """
import logging
import logging.config

logger = logging.getLogger("reconfigured.app")

logging.basicConfig(force=True)
logger.warning("after basicConfig force")

logging.config.dictConfig({"version": 1, "disable_existing_loggers": False, "root": {"handlers": []}})
logger.warning("after dictConfig")

logging.config.fileConfig(%r, disable_existing_loggers=False)
logger.warning("after fileConfig")
"""
        % str(config_file),
        env=env,
    )
    assert status == 0, err

    messages = [event["message"] for event in log_intake_receiver.events()]
    for message in ("after basicConfig force", "after dictConfig", "after fileConfig"):
        assert messages.count(message) == 1, messages


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires the fork start method")
@pytest.mark.usefixtures("log_intake_receiver")
def test_multiprocessing_fork_workers_submit_logs_with_module_cloning(
    ddtrace_run_python_code_in_subprocess, log_intake_receiver
):
    """With module cloning, the application uses a later copy of threading; fork workers must still flush."""
    env = os.environ.copy()
    env.update(
        {
            "DD_AGENTLESS_LOG_SUBMISSION_ENABLED": "true",
            "DD_API_KEY": "test-key",
            "DD_AGENTLESS_LOG_SUBMISSION_URL": log_intake_receiver.url,
            "DD_TRACE_AGENT_URL": log_intake_receiver.url,
            "DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE": "true",
        }
    )
    env.pop("DD_LOGS_OTEL_ENABLED", None)

    _, err, status, _ = ddtrace_run_python_code_in_subprocess(
        """
import logging
import multiprocessing
import threading

from ddtrace.internal import threads

# Module cloning must have given the application its own copy of threading.
assert threading is not threads._threading


def work(message):
    logging.getLogger("mp.app").warning(message)


if __name__ == "__main__":
    process = multiprocessing.get_context("fork").Process(target=work, args=("from cloned process",))
    process.start()
    process.join()
""",
        env=env,
    )
    assert status == 0, err

    messages = [event["message"] for event in log_intake_receiver.events()]
    assert messages.count("from cloned process") == 1, messages
