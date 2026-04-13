"""StderrCapture — captures C-level stderr (fd 2) and forwards to the logs intake.

Some processes write directly to file descriptor 2, bypassing Python's ``sys.stderr``
(e.g. native libraries, subprocesses).  This module redirects fd 2 through an
``os.pipe``, reads the output in a background thread, and forwards it to a
:class:`~ddtrace.testing.internal.logs.LogsWriter` while also teeing it back to the
original stderr so that the user's terminal output is preserved.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import typing as t

from ddtrace.internal.constants import LOG_ATTR_VALUE_ZERO
from ddtrace.testing.internal.writer import Event


if t.TYPE_CHECKING:
    from ddtrace.testing.internal.logs._writer import LogsWriter

_log = logging.getLogger(__name__)

# Read up to 64 KB at a time from the redirected stderr pipe.
_READ_CHUNK_SIZE = 64 * 1024


TraceContextProvider = t.Callable[[], tuple[str, str]]
"""Callable returning ``(trace_id, span_id)`` for the currently active test span."""


def _default_trace_context() -> tuple[str, str]:
    """Return trace context from the global ddtrace tracer, if available."""
    try:
        from ddtrace import tracer

        ctx = tracer.get_log_correlation_context()
        return ctx.get("dd.trace_id", LOG_ATTR_VALUE_ZERO), ctx.get("dd.span_id", LOG_ATTR_VALUE_ZERO)
    except Exception:
        return LOG_ATTR_VALUE_ZERO, LOG_ATTR_VALUE_ZERO


class StderrCapture:
    """Captures C-level stderr (fd 2) and forwards it to the Datadog logs intake.

    Any process or library that writes directly to file descriptor 2 — rather than
    going through Python's ``sys.stderr`` — is captured automatically.

    Usage::

        capture = StderrCapture(logs_writer)
        capture.start()   # redirect fd 2 → pipe, start reader thread
        ...               # all fd-2 output is captured
        capture.stop()    # restore fd 2, drain pipe, stop reader thread
    """

    def __init__(
        self,
        writer: LogsWriter,
        get_trace_context: t.Optional[TraceContextProvider] = None,
    ) -> None:
        self._writer = writer
        self._get_trace_context = get_trace_context or _default_trace_context
        self._original_stderr_fd: t.Optional[int] = None
        self._read_fd: t.Optional[int] = None
        self._reader_thread: t.Optional[threading.Thread] = None
        self._started = False

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return

        # Back up the real stderr fd so we can tee output and restore later.
        self._original_stderr_fd = os.dup(2)

        # Create a pipe: writes to fd 2 will arrive on _read_fd.
        self._read_fd, write_fd = os.pipe()
        try:
            os.dup2(write_fd, 2)
        finally:
            # The write end is now duplicated onto fd 2; close the original.
            os.close(write_fd)

        self._started = True
        self._reader_thread = threading.Thread(target=self._read_loop, name="stderr-capture-reader", daemon=True)
        self._reader_thread.start()
        _log.debug("StderrCapture: stderr (fd 2) redirected through pipe")

    def stop(self, timeout: float = 5.0) -> None:
        if not self._started:
            return

        # Restore the original stderr fd.  This closes the pipe's write end
        # (fd 2 is overwritten), which causes the reader thread to see EOF.
        if self._original_stderr_fd is not None:
            os.dup2(self._original_stderr_fd, 2)
            os.close(self._original_stderr_fd)
            self._original_stderr_fd = None

        if self._reader_thread is not None:
            self._reader_thread.join(timeout=timeout)
            if self._reader_thread.is_alive():
                _log.warning("StderrCapture: reader thread did not finish within %.1fs", timeout)
            self._reader_thread = None

        self._started = False
        _log.debug("StderrCapture: stopped, stderr restored")

    # -- background reader ----------------------------------------------------

    def _read_loop(self) -> None:
        """Continuously read from the pipe, tee to original stderr, forward to LogsWriter."""
        if self._read_fd is None or self._original_stderr_fd is None:
            _log.error("StderrCapture: _read_loop called in invalid state")
            return

        # Capture fds as locals so that stop() nulling the instance attributes
        # cannot race with our reads/writes inside this thread.
        read_fd = self._read_fd
        original_stderr_fd = self._original_stderr_fd

        buf = b""
        try:
            while True:
                chunk = os.read(read_fd, _READ_CHUNK_SIZE)
                if not chunk:
                    # EOF — the write end of the pipe has been closed (stop() was called).
                    break

                # Tee to the original stderr so the user still sees output.
                # This may raise EBADF if stop() already closed the original fd;
                # swallow it so we still process the buffered data.
                try:
                    os.write(original_stderr_fd, chunk)
                except OSError:
                    pass

                # Accumulate and split on newlines so each event is a complete line.
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._forward_line(line)

            # Flush any remaining partial line.
            if buf:
                self._forward_line(buf)
        except OSError:
            # The pipe was closed unexpectedly (e.g. process teardown).
            _log.debug("StderrCapture: pipe read error during teardown", exc_info=True)
        finally:
            os.close(read_fd)
            self._read_fd = None

    def _forward_line(self, raw_line: bytes) -> None:
        from ddtrace.testing.internal.logs import MAX_MESSAGE_BYTES
        from ddtrace.testing.internal.logs import TRUNCATION_SUFFIX

        try:
            message = raw_line.decode("utf-8", errors="replace")
        except Exception:
            return

        if not message:
            return

        if len(message) > MAX_MESSAGE_BYTES:
            message = message[: MAX_MESSAGE_BYTES - len(TRUNCATION_SUFFIX)] + TRUNCATION_SUFFIX

        trace_id, span_id = self._get_trace_context()

        # Capture the timestamp at read time, not at flush time, so buffered
        # events carry an accurate time even if the flush is delayed.
        timestamp_ms = int(time.time() * 1000)

        event: Event = {
            "date": timestamp_ms,
            "ddsource": "stderr",
            "ddtags": "datadog.product:citest",
            "hostname": self._writer.hostname,
            "message": message,
            "service": self._writer.service,
            "status": "info",
            "dd.trace_id": trace_id,
            "dd.span_id": span_id,
        }
        self._writer.put_event(event)
