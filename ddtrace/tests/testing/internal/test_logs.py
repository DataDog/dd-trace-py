"""Tests for ddtrace.testing.internal.logs module."""

from __future__ import annotations

import logging
import threading

from ddtrace.testing.internal.logs import LogsHandler
from ddtrace.testing.internal.writer import Event


class _FakeLogsWriter:
    """Minimal stand-in for LogsWriter that records put_event calls."""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self.hostname = "test-host"
        self.service = "test-service"
        self.lock = threading.Lock()

    def put_event(self, event: Event) -> None:
        with self.lock:
            self.events.append(event)


class TestLogsHandlerTruncation:
    """Tests for LogsHandler._MAX_MESSAGE_BYTES truncation and timestamp."""

    def _make_handler(self) -> tuple[LogsHandler, _FakeLogsWriter]:
        writer = _FakeLogsWriter()
        # LogsHandler expects a LogsWriter but only uses .put_event / .hostname / .service,
        # which _FakeLogsWriter provides.
        handler = LogsHandler(writer)  # type: ignore[arg-type]
        return handler, writer

    def test_short_message_not_truncated(self) -> None:
        handler, writer = self._make_handler()
        # Use WARNING so the record isn't filtered by the root logger's default level.
        record = logging.LogRecord("test", logging.WARNING, "", 0, "short msg", (), None)
        handler.emit(record)

        assert len(writer.events) == 1
        assert writer.events[0]["message"] == "short msg"
        # Timestamp should be captured from the log record's creation time, not flush time.
        assert "date" in writer.events[0]
        assert isinstance(writer.events[0]["date"], int)

    def test_long_message_truncated(self) -> None:
        handler, writer = self._make_handler()
        long_msg = "A" * (LogsHandler._MAX_MESSAGE_BYTES + 100)
        record = logging.LogRecord("test", logging.WARNING, "", 0, long_msg, (), None)
        handler.emit(record)

        assert len(writer.events) == 1
        msg = writer.events[0]["message"]
        assert msg.endswith("... [truncated]")
        assert len(msg) == LogsHandler._MAX_MESSAGE_BYTES

    def test_exact_limit_not_truncated(self) -> None:
        handler, writer = self._make_handler()
        exact_msg = "B" * LogsHandler._MAX_MESSAGE_BYTES
        record = logging.LogRecord("test", logging.WARNING, "", 0, exact_msg, (), None)
        handler.emit(record)

        assert len(writer.events) == 1
        assert "truncated" not in writer.events[0]["message"]
