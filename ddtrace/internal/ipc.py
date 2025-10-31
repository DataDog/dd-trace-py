from contextlib import contextmanager
import os
import secrets
import tempfile
import typing

from ddtrace.internal._unpatched import unpatched_open
from ddtrace.internal.compat import Path
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


MAX_FILE_SIZE = 8192


class BaseLock:
    def __init__(self, file: typing.IO[typing.Any]):
        self.file = file

    def acquire(self):
        ...

    def release(self):
        ...

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

    @contextmanager
    def open_file(path, mode):
        yield unpatched_open(path, mode)

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

    def open_file(path, mode):
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


TMPDIR = Path(tempfile.gettempdir())


class SharedStringFile:
    """A simple shared-file implementation for multiprocess communication."""

    def __init__(self) -> None:
        self.filename: typing.Optional[str] = str(TMPDIR / secrets.token_hex(8))

    def put(self, data: str) -> None:
        """Put a string into the file."""
        if self.filename is None:
            return

        try:
            with open_file(self.filename, "ab") as f, WriteLock(f):
                f.seek(0, os.SEEK_END)
                dt = (data + "\x00").encode()
                if f.tell() + len(dt) <= MAX_FILE_SIZE:
                    f.write(dt)
        except Exception:  # nosec
            pass

    def peekall(self) -> typing.List[str]:
        """Peek at all strings from the file."""
        if self.filename is None:
            return []

        try:
            with open_file(self.filename, "r+b") as f, ReadLock(f):
                f.seek(0)
                return f.read().strip(b"\x00").decode().split("\x00")
        except Exception:  # nosec
            return []

    def snatchall(self) -> typing.List[str]:
        """Retrieve and remove all strings from the file."""
        if self.filename is None:
            return []

        try:
            with open_file(self.filename, "r+b") as f, WriteLock(f):
                f.seek(0)
                strings = f.read().strip(b"\x00").decode().split("\x00")

                f.seek(0)
                f.truncate()

            return strings
        except Exception:  # nosec
            return []
