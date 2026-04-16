"""Capture output from fd-level streams or log files and forward to the logs intake.

Some native libraries write directly to file descriptor 2 (stderr) or a dedicated
log file, bypassing Python's ``sys.stderr``.  This module provides three capture
implementations that all forward captured lines to a
:class:`~ddtrace.testing.internal.logs.LogsWriter`:

- :class:`StderrCapture` — redirects fd 2 through an ``os.pipe``, tees back to the
  real stderr.
- :class:`StdoutCapture` — same mechanism for fd 1.
- :class:`FileCapture` — tails an existing file (polls for new lines after seeking
  to EOF at start).

All three share line-decoding, truncation, and event-building logic via the
:class:`_BaseCapture` mixin.
"""

from __future__ import annotations

import abc
import logging
import os
import threading
import time
import typing as t

from ddtrace.internal.constants import LOG_ATTR_VALUE_ZERO
from ddtrace.testing.internal.writer import Event


if t.TYPE_CHECKING:
    from ddtrace.testing.internal.logs import LogsWriter

_log = logging.getLogger(__name__)

# Read up to 64 KB at a time from pipes / files.
_READ_CHUNK_SIZE = 64 * 1024

# Individual messages forwarded to the intake are capped at the Datadog logs intake per-entry limit.
_MAX_MESSAGE_BYTES = 1 * 1024 * 1024  # 1 MB

_TRUNCATION_SUFFIX = "... [truncated]"

# How long FileCapture sleeps between polls when the file has no new data.
_FILE_POLL_INTERVAL = 0.05


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


# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------


class _BaseCapture(abc.ABC):
    """Shared interface, line-decoding, truncation, and event-building logic."""

    # Subclasses set these to appropriate values.
    _ddsource: str = "unknown"

    def __init__(
        self,
        writer: LogsWriter,
        get_trace_context: t.Optional[TraceContextProvider] = None,
    ) -> None:
        self._writer = writer
        self._get_trace_context = get_trace_context or _default_trace_context
        self._started = False

    @abc.abstractmethod
    def start(self) -> None:
        """Begin capturing output."""

    @abc.abstractmethod
    def stop(self, timeout: float = 5.0) -> None:
        """Stop capturing and drain any buffered output."""

    def _forward_line(self, raw_line: bytes) -> None:
        try:
            message = raw_line.decode("utf-8", errors="replace")
        except Exception:
            return

        if not message:
            return

        if len(message) > _MAX_MESSAGE_BYTES:
            message = message[: _MAX_MESSAGE_BYTES - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX

        trace_id, span_id = self._get_trace_context()

        # Capture the timestamp at read time, not at flush time, so buffered
        # events carry an accurate time even if the flush is delayed.
        timestamp_ms = int(time.time() * 1000)

        event: Event = {
            "date": timestamp_ms,
            "ddsource": self._ddsource,
            "ddtags": "datadog.product:citest",
            "hostname": self._writer.hostname,
            "message": message,
            "service": self._writer.service,
            "status": "info",
            "dd.trace_id": trace_id,
            "dd.span_id": span_id,
        }
        self._writer.put_event(event)


# ---------------------------------------------------------------------------
# Fork safety for fd-level captures
# ---------------------------------------------------------------------------

# Module-level set of active fd captures.  After fork the child inherits
# redirected fds but *not* the reader threads, so writes to the fd would
# fill the pipe buffer and block.  The at-fork handler restores the fds.
_active_fd_captures: set[t.Any] = set()


def _after_fork_in_child() -> None:
    """Restore original fds in the child process after fork."""
    for capture in list(_active_fd_captures):
        capture._restore_in_forked_child()
    _active_fd_captures.clear()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork_in_child)


# ---------------------------------------------------------------------------
# Fd-level capture (stderr / stdout)
# ---------------------------------------------------------------------------


class _BaseFdCapture(_BaseCapture):
    r"""Redirects a file descriptor through an ``os.pipe``, capturing all writes.

    The captured output is teed back to the original fd so the user's terminal
    output is preserved.  Each complete line (``\n``-delimited) is forwarded as
    a separate log event.
    """

    # Subclasses must set this to the fd number they capture (1 or 2).
    _fd: int

    def __init__(
        self,
        writer: LogsWriter,
        get_trace_context: t.Optional[TraceContextProvider] = None,
    ) -> None:
        super().__init__(writer, get_trace_context)
        self._original_fd: t.Optional[int] = None
        self._read_fd: t.Optional[int] = None
        self._reader_thread: t.Optional[threading.Thread] = None

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return

        # Back up the real fd so we can tee output and restore later.
        self._original_fd = os.dup(self._fd)

        # Create a pipe: writes to the fd will arrive on _read_fd.
        self._read_fd, write_fd = os.pipe()
        try:
            os.dup2(write_fd, self._fd)
        finally:
            # The write end is now duplicated onto the target fd; close the original.
            os.close(write_fd)

        self._started = True
        _active_fd_captures.add(self)
        self._reader_thread = threading.Thread(
            target=self._read_loop,
            name=f"fd{self._fd}-capture-reader",
            daemon=True,
        )
        self._reader_thread.start()
        _log.debug("%s: fd %d redirected through pipe", type(self).__name__, self._fd)

    def stop(self, timeout: float = 5.0) -> None:
        if not self._started:
            return

        _active_fd_captures.discard(self)

        # Restore the original fd.  This closes the pipe's write end (fd is overwritten),
        # which causes the reader thread to see EOF.
        if self._original_fd is not None:
            os.dup2(self._original_fd, self._fd)
            os.close(self._original_fd)
            self._original_fd = None

        if self._reader_thread is not None:
            self._reader_thread.join(timeout=timeout)
            if self._reader_thread.is_alive():
                _log.warning("%s: reader thread did not finish within %.1fs", type(self).__name__, timeout)
            self._reader_thread = None

        self._started = False
        _log.debug("%s: stopped, fd %d restored", type(self).__name__, self._fd)

    def _restore_in_forked_child(self) -> None:
        """Restore the original fd in a forked child process.

        After ``os.fork()`` the child inherits the redirected fd but not the
        reader thread.  Without restoration, writes to the fd fill the pipe
        buffer (nobody is reading) and eventually block the child.
        """
        if self._original_fd is not None:
            try:
                os.dup2(self._original_fd, self._fd)
                os.close(self._original_fd)
            except OSError:
                pass
            self._original_fd = None
        if self._read_fd is not None:
            try:
                os.close(self._read_fd)
            except OSError:
                pass
            self._read_fd = None
        self._started = False
        self._reader_thread = None

    # -- background reader ----------------------------------------------------

    def _read_loop(self) -> None:
        """Continuously read from the pipe, tee to original fd, forward to LogsWriter."""
        if self._read_fd is None or self._original_fd is None:
            _log.error("%s: _read_loop called in invalid state", type(self).__name__)
            return

        # Capture fds as locals so that stop() nulling the instance attributes
        # cannot race with our reads/writes inside this thread.
        read_fd = self._read_fd
        original_fd = self._original_fd

        buf = b""
        try:
            while True:
                chunk = os.read(read_fd, _READ_CHUNK_SIZE)
                if not chunk:
                    # EOF — the write end of the pipe has been closed (stop() was called).
                    break

                # Tee to the original fd so the user still sees output.
                # This may raise EBADF if stop() already closed the original fd;
                # swallow it so we still process the buffered data.
                try:
                    os.write(original_fd, chunk)
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
            _log.debug("%s: pipe read error during teardown", type(self).__name__, exc_info=True)
        finally:
            os.close(read_fd)
            self._read_fd = None


class StderrCapture(_BaseFdCapture):
    """Captures C-level stderr (fd 2) and forwards it to the Datadog logs intake.

    Any process or library that writes directly to file descriptor 2 — rather than
    going through Python's ``sys.stderr`` — is captured automatically.

    Usage::

        capture = StderrCapture(logs_writer)
        capture.start()   # redirect fd 2 → pipe, start reader thread
        ...               # all fd-2 output is captured
        capture.stop()    # restore fd 2, drain pipe, stop reader thread
    """

    _fd = 2
    _ddsource = "stderr"


class StdoutCapture(_BaseFdCapture):
    """Captures C-level stdout (fd 1) and forwards it to the Datadog logs intake.

    Same pipe-based mechanism as :class:`StderrCapture` but for fd 1.

    .. warning::
        pytest and many test frameworks capture fd 1 themselves.  Enable this
        only when your native code bypasses Python and writes directly to fd 1.
    """

    _fd = 1
    _ddsource = "stdout"


# ---------------------------------------------------------------------------
# File-based capture (tail -f style)
# ---------------------------------------------------------------------------


class FileCapture(_BaseCapture):
    """Tails a file and forwards new lines to the Datadog logs intake.

    Opens the file at :meth:`start`, seeks to EOF so existing content is not
    replayed, then polls for new data in a background thread.  No fd redirection
    is performed — the original file continues to receive writes normally.

    Usage::

        capture = FileCapture("/tmp/nccl.log", logs_writer)
        capture.start()   # open file, seek to end, start reader thread
        ...
        capture.stop()    # signal reader thread, drain remaining lines
    """

    def __init__(
        self,
        path: str,
        writer: LogsWriter,
        get_trace_context: t.Optional[TraceContextProvider] = None,
    ) -> None:
        super().__init__(writer, get_trace_context)
        self._path = path
        self._ddsource = os.path.basename(path)
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._reader_thread: t.Optional[threading.Thread] = None

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return

        self._stop_event.clear()
        self._ready_event.clear()
        self._started = True
        self._reader_thread = threading.Thread(
            target=self._read_loop,
            name=f"file-capture-reader:{self._path}",
            daemon=True,
        )
        self._reader_thread.start()
        # Wait until the thread has opened the file and seeked to EOF so that
        # any writes on the calling thread after start() returns are not missed.
        self._ready_event.wait(timeout=2.0)
        _log.debug("FileCapture: tailing %s", self._path)

    def stop(self, timeout: float = 5.0) -> None:
        if not self._started:
            return

        self._stop_event.set()

        if self._reader_thread is not None:
            self._reader_thread.join(timeout=timeout)
            if self._reader_thread.is_alive():
                _log.warning("FileCapture: reader thread did not finish within %.1fs", timeout)
            self._reader_thread = None

        self._started = False
        _log.debug("FileCapture: stopped tailing %s", self._path)

    # -- background reader ----------------------------------------------------

    def _read_loop(self) -> None:
        """Poll the file for new lines and forward each one to LogsWriter."""
        try:
            f = self._open_or_wait()
            if f is None:
                return  # stop() was called before the file appeared
            with f:
                buf = b""
                while not self._stop_event.is_set():
                    chunk = f.read(_READ_CHUNK_SIZE)
                    if chunk:
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            self._forward_line(line)
                    else:
                        time.sleep(_FILE_POLL_INTERVAL)

                # Drain any data written between the last poll and stop().
                chunk = f.read()
                if chunk:
                    buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._forward_line(line)
                if buf:
                    self._forward_line(buf)
        except OSError:
            _log.debug("FileCapture: error reading %s", self._path, exc_info=True)

    def _open_or_wait(self) -> t.Optional[t.IO[bytes]]:
        """Open the file, waiting for it to appear if it doesn't exist yet.

        If the file already exists, it is seeked to EOF so pre-existing content
        is not replayed.  If the file had to be waited for, it is read from the
        beginning — all content in a newly-created file is considered "new".

        Returns the opened file or ``None`` if ``stop()`` is called before the
        file appears.
        """
        waited = False
        while not self._stop_event.is_set():
            try:
                f = open(self._path, "rb")  # noqa: SIM115
            except FileNotFoundError:
                if not waited:
                    _log.debug("FileCapture: %s not found yet, waiting…", self._path)
                    self._ready_event.set()  # unblock start() so caller is not stuck
                waited = True
                time.sleep(_FILE_POLL_INTERVAL)
                continue
            if not waited:
                f.seek(0, 2)  # existing file — skip old content
            self._ready_event.set()
            return f
        self._ready_event.set()
        return None
