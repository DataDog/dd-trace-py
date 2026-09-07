from contextlib import contextmanager
import io
import os
from pathlib import Path
import secrets
import tempfile
import types
import typing

from ddtrace.internal import forksafe
from ddtrace.internal._unpatched import unpatched_open
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


MAX_FILE_SIZE = 8192

# The shared file is always opened in binary mode, whichever locking backend is
# in use.
SharedFile = typing.IO[bytes]


class _DummyFile(io.BytesIO):
    """Stand-in yielded when the shared file is unavailable.

    Callers still get a usable file object, so they run to completion instead of
    having to handle an exception, but nothing written to it is shared with any
    other process. put_unlocked reports writes to it as failures so that callers
    which retry, or which remember what they have already sent, are not told the
    data was stored.
    """


class BaseLock:
    def __init__(self, file: SharedFile) -> None:
        self.file = file

    def acquire(self) -> None: ...

    def release(self) -> None: ...

    def __enter__(self) -> "BaseLock":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: typing.Optional[type[BaseException]],
        exc_value: typing.Optional[BaseException],
        exc_tb: typing.Optional[types.TracebackType],
    ) -> None:
        self.release()


try:
    # Unix based file locking
    # Availability: Unix, not Emscripten, not WASI.
    import fcntl

    class BaseUnixLock(BaseLock):
        __acquire_mode__: typing.Optional[int] = None

        def acquire(self) -> None:
            if self.__acquire_mode__ is None:
                msg = f"Cannot use lock of type {type(self)} directly"
                raise ValueError(msg)

            fcntl.lockf(self.file, self.__acquire_mode__)

        def release(self) -> None:
            fcntl.lockf(self.file, fcntl.LOCK_UN)

    class ReadLock(BaseUnixLock):
        __acquire_mode__ = fcntl.LOCK_SH

    class WriteLock(BaseUnixLock):
        __acquire_mode__ = fcntl.LOCK_EX

    open_file = unpatched_open

except ModuleNotFoundError:
    # Availability: Windows
    #
    # The defs in this branch are deliberately left unannotated: their bodies
    # call msvcrt/_winapi APIs that only exist in the Windows stubs, and mypy
    # runs against Linux/macOS, where checking them would report every call as
    # a missing attribute.
    import msvcrt

    class BaseWinLock(BaseLock):
        def acquire(self):
            f = self.file
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_RLCK, MAX_FILE_SIZE)

        def release(self):
            f = self.file
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, MAX_FILE_SIZE)

    ReadLock = WriteLock = BaseWinLock  # type: ignore

    def open_file(path, mode):  # type: ignore
        import _winapi

        # force all modes to be read/write binary
        mode = "r+b"
        flag = _winapi.GENERIC_READ | _winapi.GENERIC_WRITE
        fd_flag = os.O_RDWR | os.O_CREAT | os.O_BINARY | os.O_RANDOM
        SHARED_READ_WRITE = 0x7
        OPEN_ALWAYS = 4
        RANDOM_ACCESS = 0x10000000
        handle = _winapi.CreateFile(path, flag, SHARED_READ_WRITE, 0, OPEN_ALWAYS, RANDOM_ACCESS, 0)
        fd = msvcrt.open_osfhandle(handle, fd_flag | os.O_NOINHERIT)
        return unpatched_open(fd, mode)


try:
    TMPDIR: typing.Optional[Path] = Path(tempfile.gettempdir())
except FileNotFoundError:
    TMPDIR = None


class SharedStringFile:
    """A simple shared-file implementation for multiprocess communication."""

    def __init__(self, name: typing.Optional[str] = None) -> None:
        self.filename: typing.Optional[str] = (
            str(TMPDIR / (name or secrets.token_hex(8))) if TMPDIR is not None else None
        )
        if self.filename is not None:
            try:
                Path(self.filename).touch(exist_ok=True)
            except OSError:
                # The temp dir is not writable, or the file exists but belongs to
                # another user (e.g. it was created before dropping privileges).
                log.debug("Cannot create shared file %s; disabling it", self.filename, exc_info=True)
                self.filename = None
        # Thread-level lock to serialize access within the same process.
        # POSIX advisory file locks (fcntl.lockf) do NOT block threads within
        # the same process, so concurrent put/snatchall from different threads
        # would race on the Python write buffer vs OS flush window.
        # forksafe.Lock() resets after fork so children don't inherit a locked mutex.
        self._file_thread_lock = forksafe.Lock()

    def put_unlocked(self, f: SharedFile, data: str) -> bool:
        if isinstance(f, _DummyFile):
            return False
        f.seek(0, os.SEEK_END)
        dt = (data + "\x00").encode()
        if f.tell() + len(dt) <= MAX_FILE_SIZE:
            f.write(dt)
            return True
        return False

    def put(self, data: str) -> bool:
        """Put a string into the file. Returns True on success, False on failure."""
        if self.filename is None:
            return False

        try:
            with self.lock_exclusive() as f:
                return self.put_unlocked(f, data)
        except Exception:  # nosec
            return False

    def peekall_unlocked(self, f: SharedFile) -> list[str]:
        f.seek(0)
        return data.decode().split("\x00") if (data := f.read().strip(b"\x00")) else []

    def peekall(self) -> list[str]:
        """Peek at all strings from the file."""
        if self.filename is None:
            return []

        try:
            with self.lock_shared() as f:
                return self.peekall_unlocked(f)
        except Exception:  # nosec
            return []

    def snatchall(self) -> list[str]:
        """Retrieve and remove all strings from the file."""
        if self.filename is None:
            return []

        try:
            with self.lock_exclusive() as f:
                try:
                    return self.peekall_unlocked(f)
                finally:
                    self.clear_unlocked(f)
        except Exception:  # nosec
            return []

    def clear_unlocked(self, f: SharedFile) -> None:
        f.seek(0)
        f.truncate()

    def clear(self) -> None:
        """Clear all strings from the file."""
        if self.filename is None:
            return

        try:
            with self.lock_exclusive() as f:
                self.clear_unlocked(f)
        except Exception:  # nosec
            pass

    def _try_open(self, filename: str, mode: str) -> typing.Optional[SharedFile]:
        # Open the shared file, returning None if it cannot be opened. A
        # PermissionError is not transient (the file belongs to another user,
        # e.g. it was created before the process dropped privileges), so the
        # shared file is disabled for good rather than failing on every access.
        try:
            return open_file(filename, mode)
        except PermissionError:
            log.debug("Cannot open shared file %s; disabling it", filename, exc_info=True)
            self.filename = None
        except OSError:
            log.debug("Cannot open shared file %s", filename, exc_info=True)
        return None

    @contextmanager
    def lock_shared(self) -> typing.Iterator[SharedFile]:
        """Context manager to acquire a shared/read lock on the file."""
        if self.filename is None:
            # No writable temp dir (e.g. readOnlyRootFilesystem).
            yield _DummyFile()
            return
        with self._file_thread_lock:
            if (f := self._try_open(self.filename, "rb")) is None:
                yield _DummyFile()
                return
            with f, ReadLock(f):
                yield f

    @contextmanager
    def lock_exclusive(self) -> typing.Iterator[SharedFile]:
        """Context manager to acquire an exclusive/write lock on the file."""
        if self.filename is None:
            # No writable temp dir (e.g. readOnlyRootFilesystem).
            yield _DummyFile()
            return
        # Acquire the thread-level lock first to prevent same-process threads
        # from bypassing the POSIX file lock (fcntl.lockf only blocks across
        # processes, not within the same process).
        with self._file_thread_lock:
            if (f := self._try_open(self.filename, "r+b")) is None:
                yield _DummyFile()
                return
            with f, WriteLock(f):
                yield f
                # Flush before releasing the lock. Here we first release the lock,
                # then close the file. If a read happens in between these two
                # operations, the reader might see outdated data.
                f.flush()
