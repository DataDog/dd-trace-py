from contextlib import contextmanager
import os
from pathlib import Path
import secrets
import tempfile
import typing

from ddtrace.internal._unpatched import unpatched_open
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


MAX_FILE_SIZE = 8192


class BaseLock:
    def __init__(self, file: typing.IO[typing.Any]):
        self.file = file

    def acquire(self): ...

    def release(self): ...

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        self.release()


try:
    # Unix based file locking
    # Availability: Unix, not Emscripten, not WASI.
    import fcntl

    class BaseUnixLock(BaseLock):
        __acquire_mode__: typing.Optional[int] = None

        def acquire(self):
            if self.__acquire_mode__ is None:
                msg = f"Cannot use lock of type {type(self)} directly"
                raise ValueError(msg)

            fcntl.lockf(self.file, self.__acquire_mode__)

        def release(self):
            fcntl.lockf(self.file, fcntl.LOCK_UN)

    class ReadLock(BaseUnixLock):
        __acquire_mode__ = fcntl.LOCK_SH

    class WriteLock(BaseUnixLock):
        __acquire_mode__ = fcntl.LOCK_EX

    open_file = unpatched_open

except ModuleNotFoundError:
    # Availability: Windows
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
            Path(self.filename).touch(exist_ok=True)

    def put_unlocked(self, f: typing.BinaryIO, data: str) -> None:
        f.seek(0, os.SEEK_END)
        dt = (data + "\x00").encode()
        if f.tell() + len(dt) <= MAX_FILE_SIZE:
            f.write(dt)

    def put(self, data: str) -> None:
        """Put a string into the file."""
        if self.filename is None:
            return

        try:
            with self.lock_exclusive() as f:
                self.put_unlocked(f, data)
        except Exception:  # nosec
            pass

    def peekall_unlocked(self, f: typing.BinaryIO) -> typing.List[str]:
        f.seek(0)
        return data.decode().split("\x00") if (data := f.read().strip(b"\x00")) else []

    def peekall(self) -> typing.List[str]:
        """Peek at all strings from the file."""
        if self.filename is None:
            return []

        try:
            with self.lock_shared() as f:
                return self.peekall_unlocked(f)
        except Exception:  # nosec
            return []

    def snatchall(self) -> typing.List[str]:
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

    def clear_unlocked(self, f: typing.BinaryIO) -> None:
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

    @contextmanager
    def lock_shared(self):
        """Context manager to acquire a shared/read lock on the file."""
        with open_file(self.filename, "rb") as f, ReadLock(f):
            yield f

    @contextmanager
    def lock_exclusive(self):
        """Context manager to acquire an exclusive/write lock on the file."""
        if self.filename is None:
            return
        with open_file(self.filename, "r+b") as f, WriteLock(f):
            yield f
            # Flush before releasing the lock. Here we first release the lock,
            # then close the file. If a read happens in between these two
            # operations, the reader might see outdated data.
            f.flush()
