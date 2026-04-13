"""Tests for ddtrace.testing.internal.stderr_capture module."""

from __future__ import annotations

import os
import tempfile
import threading
import time

import pytest

from ddtrace.testing.internal.stderr_capture import FileCapture
from ddtrace.testing.internal.stderr_capture import StderrCapture
from ddtrace.testing.internal.stderr_capture import StdoutCapture
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
            big_line = b"X" * (1024 * 1024 + 100) + b"\n"
            os.write(2, big_line)
            time.sleep(0.5)
        finally:
            capture.stop()

        assert len(writer.events) >= 1
        assert writer.events[0]["message"].endswith("... [truncated]")
        assert len(writer.events[0]["message"]) == 1024 * 1024

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


# ---------------------------------------------------------------------------
# StdoutCapture tests
# ---------------------------------------------------------------------------


class TestStdoutCaptureBasic:
    """Basic smoke tests for StdoutCapture — mirrors the StderrCapture tests."""

    def test_captures_c_level_stdout(self) -> None:
        writer = _FakeLogsWriter()
        capture = StdoutCapture(writer, get_trace_context=lambda: ("333", "444"))  # type: ignore[arg-type]
        capture.start()
        try:
            os.write(1, b"STDOUT LINE\n")
            time.sleep(0.2)
        finally:
            capture.stop()

        assert len(writer.events) >= 1
        event = writer.events[0]
        assert event["message"] == "STDOUT LINE"
        assert event["ddsource"] == "stdout"
        assert event["dd.trace_id"] == "333"

    def test_start_and_stop(self) -> None:
        writer = _FakeLogsWriter()
        capture = StdoutCapture(writer, get_trace_context=lambda: ("0", "0"))  # type: ignore[arg-type]
        capture.start()
        assert capture._started
        capture.stop()
        assert not capture._started

    def test_stop_without_start_is_safe(self) -> None:
        writer = _FakeLogsWriter()
        capture = StdoutCapture(writer, get_trace_context=lambda: ("0", "0"))  # type: ignore[arg-type]
        capture.stop()  # should not raise


# ---------------------------------------------------------------------------
# FileCapture tests
# ---------------------------------------------------------------------------


class TestFileCapture:
    """Tests for FileCapture (tail-style file reading)."""

    def test_captures_lines_written_after_start(self) -> None:
        writer = _FakeLogsWriter()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".log") as f:
            path = f.name
            # Pre-existing content should NOT be replayed.
            f.write(b"old line\n")

        try:
            capture = FileCapture(path, writer, get_trace_context=lambda: ("555", "666"))  # type: ignore[arg-type]
            capture.start()
            try:
                with open(path, "ab") as f:
                    f.write(b"new line\n")
                    f.flush()
                time.sleep(0.3)
            finally:
                capture.stop()
        finally:
            os.unlink(path)

        messages = [e["message"] for e in writer.events]
        assert "new line" in messages
        assert "old line" not in messages

    def test_ddsource_is_filename(self) -> None:
        writer = _FakeLogsWriter()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".log", prefix="nccl_") as f:
            path = f.name

        try:
            capture = FileCapture(path, writer, get_trace_context=lambda: ("0", "0"))  # type: ignore[arg-type]
            capture.start()
            try:
                with open(path, "ab") as f:
                    f.write(b"hello\n")
                    f.flush()
                time.sleep(0.3)
            finally:
                capture.stop()
        finally:
            os.unlink(path)

        assert len(writer.events) >= 1
        assert writer.events[0]["ddsource"] == os.path.basename(path)

    def test_multiline_file_output(self) -> None:
        writer = _FakeLogsWriter()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".log") as f:
            path = f.name

        try:
            capture = FileCapture(path, writer, get_trace_context=lambda: ("0", "0"))  # type: ignore[arg-type]
            capture.start()
            try:
                with open(path, "ab") as f:
                    f.write(b"alpha\nbeta\ngamma\n")
                    f.flush()
                time.sleep(0.3)
            finally:
                capture.stop()
        finally:
            os.unlink(path)

        messages = [e["message"] for e in writer.events]
        assert "alpha" in messages
        assert "beta" in messages
        assert "gamma" in messages

    def test_start_and_stop(self) -> None:
        writer = _FakeLogsWriter()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".log") as f:
            path = f.name

        try:
            capture = FileCapture(path, writer, get_trace_context=lambda: ("0", "0"))  # type: ignore[arg-type]
            capture.start()
            assert capture._started
            capture.stop()
            assert not capture._started
        finally:
            os.unlink(path)

    def test_stop_without_start_is_safe(self) -> None:
        writer = _FakeLogsWriter()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".log") as f:
            path = f.name

        try:
            capture = FileCapture(path, writer, get_trace_context=lambda: ("0", "0"))  # type: ignore[arg-type]
            capture.stop()  # should not raise
        finally:
            os.unlink(path)

    def test_waits_for_file_to_appear(self) -> None:
        """If the file doesn't exist at start(), capture waits and picks it up once created."""
        writer = _FakeLogsWriter()
        tmp_dir = tempfile.mkdtemp()
        path = os.path.join(tmp_dir, "lazy.log")

        try:
            capture = FileCapture(path, writer, get_trace_context=lambda: ("0", "0"))  # type: ignore[arg-type]
            capture.start()

            # File doesn't exist yet — give the poll loop a few cycles.
            time.sleep(0.2)
            assert len(writer.events) == 0

            # Now create the file and write a line.
            with open(path, "wb") as f:
                f.write(b"late arrival\n")
                f.flush()
            time.sleep(0.3)

            capture.stop()
        finally:
            if os.path.exists(path):
                os.unlink(path)
            os.rmdir(tmp_dir)

        messages = [e["message"] for e in writer.events]
        assert "late arrival" in messages

    def test_stop_before_file_appears(self) -> None:
        """stop() should not hang if the file never appears."""
        writer = _FakeLogsWriter()
        tmp_dir = tempfile.mkdtemp()
        path = os.path.join(tmp_dir, "never_created.log")

        try:
            capture = FileCapture(path, writer, get_trace_context=lambda: ("0", "0"))  # type: ignore[arg-type]
            capture.start()
            capture.stop()  # should return promptly
            assert not capture._started
        finally:
            os.rmdir(tmp_dir)


# ---------------------------------------------------------------------------
# Fork safety tests
# ---------------------------------------------------------------------------


@pytest.mark.subprocess()
def test_fork_restores_stderr_in_child():
    """After fork, the child's fd 2 should be the original stderr, not the pipe."""
    import os
    import threading

    from ddtrace.testing.internal.stderr_capture import StderrCapture

    class _FakeWriter:
        def __init__(self):
            self.events = []
            self.hostname = "h"
            self.service = "s"
            self.lock = threading.Lock()

        def put_event(self, event):
            with self.lock:
                self.events.append(event)

    writer = _FakeWriter()
    capture = StderrCapture(writer, get_trace_context=lambda: ("0", "0"))

    original_stat = os.fstat(2)
    capture.start()
    try:
        comm_read, comm_write = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(comm_read)
            try:
                child_stat = os.fstat(2)
                restored = child_stat.st_dev == original_stat.st_dev and child_stat.st_ino == original_stat.st_ino
                os.write(comm_write, b"1" if restored else b"0")
            except Exception:
                os.write(comm_write, b"E")
            finally:
                os.close(comm_write)
                os._exit(0)
        else:
            os.close(comm_write)
            result = os.read(comm_read, 1)
            os.close(comm_read)
            os.waitpid(pid, 0)
            assert result == b"1", "fd 2 was not restored in forked child"
    finally:
        capture.stop()


@pytest.mark.subprocess()
def test_fork_child_can_write_to_stderr():
    """The child should be able to write to fd 2 without blocking."""
    import os
    import threading

    from ddtrace.testing.internal.stderr_capture import StderrCapture

    class _FakeWriter:
        def __init__(self):
            self.events = []
            self.hostname = "h"
            self.service = "s"
            self.lock = threading.Lock()

        def put_event(self, event):
            with self.lock:
                self.events.append(event)

    writer = _FakeWriter()
    capture = StderrCapture(writer, get_trace_context=lambda: ("0", "0"))
    capture.start()
    try:
        comm_read, comm_write = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(comm_read)
            try:
                os.write(2, b"child stderr write\n")
                os.write(comm_write, b"1")
            except Exception:
                os.write(comm_write, b"0")
            finally:
                os.close(comm_write)
                os._exit(0)
        else:
            os.close(comm_write)
            result = os.read(comm_read, 1)
            os.close(comm_read)
            os.waitpid(pid, 0)
            assert result == b"1", "child could not write to stderr"
    finally:
        capture.stop()


@pytest.mark.subprocess()
def test_parent_capture_continues_after_fork():
    """Fork should not disrupt the parent's capture."""
    import os
    import threading
    import time

    from ddtrace.testing.internal.stderr_capture import StderrCapture

    class _FakeWriter:
        def __init__(self):
            self.events = []
            self.hostname = "h"
            self.service = "s"
            self.lock = threading.Lock()

        def put_event(self, event):
            with self.lock:
                self.events.append(event)

    writer = _FakeWriter()
    capture = StderrCapture(writer, get_trace_context=lambda: ("0", "0"))
    capture.start()
    try:
        os.write(2, b"before fork\n")
        time.sleep(0.1)

        pid = os.fork()
        if pid == 0:
            os._exit(0)
        else:
            os.waitpid(pid, 0)

        os.write(2, b"after fork\n")
        time.sleep(0.1)
    finally:
        capture.stop()

    messages = [e["message"] for e in writer.events]
    assert "before fork" in messages
    assert "after fork" in messages


# ---------------------------------------------------------------------------
# Nested fd capture (pytest capfd compatibility)
# ---------------------------------------------------------------------------


class TestNestedFdCapture:
    """Verify that our capture cooperates with other fd-level redirectors (e.g. capfd)."""

    def test_nested_redirect_does_not_lose_output(self) -> None:
        """Simulates pytest's capfd: another component saves / redirects / restores fd 2.

        Timeline:
        1. Our capture redirects fd 2 → our pipe
        2. "capfd" saves our pipe, redirects fd 2 → capfd pipe
        3. Writes during capfd go to capfd (our capture sees nothing — expected)
        4. "capfd" restores fd 2 → our pipe
        5. Writes after capfd go to our capture again
        """
        writer = _FakeLogsWriter()
        capture = StderrCapture(writer, get_trace_context=lambda: ("0", "0"))  # type: ignore[arg-type]
        capture.start()
        try:
            # Phase 1: write before "capfd" — goes to our capture.
            os.write(2, b"before capfd\n")
            time.sleep(0.1)

            # Phase 2: simulate capfd setup — save fd 2, redirect to a new pipe.
            capfd_read, capfd_write = os.pipe()
            saved_fd = os.dup(2)
            os.dup2(capfd_write, 2)
            os.close(capfd_write)

            # Write during "capfd" — goes to capfd's pipe, not ours.
            os.write(2, b"during capfd\n")

            # Read what capfd captured.
            capfd_output = b""
            while True:
                # Non-blocking: capfd_read has data, read until we've got the line.
                chunk = os.read(capfd_read, 4096)
                capfd_output += chunk
                if b"\n" in capfd_output:
                    break

            # Phase 3: simulate capfd teardown — restore fd 2 to our pipe.
            os.dup2(saved_fd, 2)
            os.close(saved_fd)
            os.close(capfd_read)

            # Phase 4: write after "capfd" — goes to our capture again.
            os.write(2, b"after capfd\n")
            time.sleep(0.1)
        finally:
            capture.stop()

        our_messages = [e["message"] for e in writer.events]
        assert "before capfd" in our_messages
        assert "after capfd" in our_messages
        # "during capfd" should NOT appear in our capture — capfd intercepted it.
        assert "during capfd" not in our_messages
        # capfd should have captured "during capfd".
        assert b"during capfd" in capfd_output
