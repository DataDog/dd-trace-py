"""Tests for ddtrace.testing.internal.logs.StderrCapture."""

from __future__ import annotations

import os
import threading
import time

from ddtrace.testing.internal.logs import MAX_MESSAGE_BYTES
from ddtrace.testing.internal.logs import StderrCapture
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


# ---------------------------------------------------------------------------
# StderrCapture tests
# ---------------------------------------------------------------------------


class TestStderrCaptureBasic:
    """Basic lifecycle tests for StderrCapture."""

    def test_start_and_stop(self) -> None:
        writer = _FakeLogsWriter()
        capture = StderrCapture(writer, get_trace_context=lambda: ("0", "0"))  # type: ignore[arg-type]
        capture.start()
        assert capture._started
        capture.stop()
        assert not capture._started

    def test_double_start_is_idempotent(self) -> None:
        writer = _FakeLogsWriter()
        capture = StderrCapture(writer, get_trace_context=lambda: ("0", "0"))  # type: ignore[arg-type]
        capture.start()
        capture.start()  # should not raise
        capture.stop()

    def test_stop_without_start_is_safe(self) -> None:
        writer = _FakeLogsWriter()
        capture = StderrCapture(writer, get_trace_context=lambda: ("0", "0"))  # type: ignore[arg-type]
        capture.stop()  # should not raise


class TestStderrCaptureRedirect:
    """Test that C-level stderr writes are captured and forwarded."""

    def test_captures_c_level_stderr(self) -> None:
        writer = _FakeLogsWriter()
        capture = StderrCapture(writer, get_trace_context=lambda: ("111", "222"))  # type: ignore[arg-type]
        capture.start()
        try:
            # Write directly to fd 2 (C-level stderr), bypassing sys.stderr.
            os.write(2, b"NCCL INFO: rank 0 connected\n")
            # Give the reader thread time to process.
            time.sleep(0.2)
        finally:
            capture.stop()

        assert len(writer.events) >= 1
        event = writer.events[0]
        assert event["message"] == "NCCL INFO: rank 0 connected"
        assert event["ddsource"] == "stderr"
        assert event["dd.trace_id"] == "111"
        assert event["dd.span_id"] == "222"
        assert event["hostname"] == "test-host"
        assert event["service"] == "test-service"
        assert "date" in event
        assert isinstance(event["date"], int)

    def test_tees_to_original_stderr(self) -> None:
        """Output should still appear on the real stderr."""
        writer = _FakeLogsWriter()
        capture = StderrCapture(writer, get_trace_context=lambda: ("0", "0"))  # type: ignore[arg-type]

        # Save a dup of stderr so we can read from it.
        read_fd, write_fd = os.pipe()
        original_stderr_fd = os.dup(2)
        os.dup2(write_fd, 2)
        os.close(write_fd)

        try:
            capture.start()
            os.write(2, b"tee test line\n")
            time.sleep(0.2)
            capture.stop()
        finally:
            os.dup2(original_stderr_fd, 2)
            os.close(original_stderr_fd)

        # Read what was teed to the "original stderr" (our pipe).
        teed_output = b""
        while True:
            chunk = os.read(read_fd, 4096)
            if not chunk:
                break
            teed_output += chunk
            if b"\n" in teed_output:
                break
        os.close(read_fd)

        assert b"tee test line" in teed_output

    def test_multiline_output(self) -> None:
        writer = _FakeLogsWriter()
        capture = StderrCapture(writer, get_trace_context=lambda: ("0", "0"))  # type: ignore[arg-type]
        capture.start()
        try:
            os.write(2, b"line one\nline two\nline three\n")
            time.sleep(0.2)
        finally:
            capture.stop()

        messages = [e["message"] for e in writer.events]
        assert "line one" in messages
        assert "line two" in messages
        assert "line three" in messages

    def test_large_output_is_truncated(self) -> None:
        writer = _FakeLogsWriter()
        capture = StderrCapture(writer, get_trace_context=lambda: ("0", "0"))  # type: ignore[arg-type]
        capture.start()
        try:
            # Write a single line larger than _MAX_MESSAGE_BYTES (1 MB).
            big_line = b"X" * (MAX_MESSAGE_BYTES + 100) + b"\n"
            os.write(2, big_line)
            time.sleep(0.5)
        finally:
            capture.stop()

        assert len(writer.events) >= 1
        assert writer.events[0]["message"].endswith("... [truncated]")
        assert len(writer.events[0]["message"]) == MAX_MESSAGE_BYTES

    def test_trace_context_updates_between_lines(self) -> None:
        """Each line should pick up the current trace context at the time it is read."""
        context = [("trace_1", "span_1")]

        def get_ctx() -> tuple[str, str]:
            return context[0]

        writer = _FakeLogsWriter()
        capture = StderrCapture(writer, get_trace_context=get_ctx)  # type: ignore[arg-type]
        capture.start()
        try:
            os.write(2, b"during test 1\n")
            time.sleep(0.2)

            context[0] = ("trace_2", "span_2")
            os.write(2, b"during test 2\n")
            time.sleep(0.2)
        finally:
            capture.stop()

        assert len(writer.events) >= 2
        first = next(e for e in writer.events if e["message"] == "during test 1")
        second = next(e for e in writer.events if e["message"] == "during test 2")
        assert first["dd.trace_id"] == "trace_1"
        assert second["dd.trace_id"] == "trace_2"
