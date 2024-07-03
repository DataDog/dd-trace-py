import os
import os.path
import secrets
import sys
import tempfile
import typing

from ddtrace.internal._unpatched import unpatched_open


try:
    # Unix based file locking
    # Availability: Unix, not Emscripten, not WASI.
    import fcntl

    def lock(f):
        fcntl.lockf(f, fcntl.LOCK_EX)

    def unlock(f):
        fcntl.lockf(f, fcntl.LOCK_UN)

    def open_file(path, mode):
        return unpatched_open(path, mode)

except ModuleNotFoundError:
    # Availability: Windows
    import msvcrt

    def size(f):
        return os.path.getsize(os.path.realpath(f.name))

    def lock(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_RLCK, size(f))

    def unlock(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, size(f))

    def open_file(path, mode):
        import _winapi

        flag = {"ab": _winapi.GENERIC_WRITE, "r+b": _winapi.GENERIC_READ | _winapi.GENERIC_WRITE}[mode]
        SHARED_READ_WRITE = 0x7
        handle = _winapi.CreateFile(path, flag, SHARED_READ_WRITE, None, _winapi.OPEN_ALWAYS, 0, None)
        fd = msvcrt.open_osfhandle(handle, 0)
        return unpatched_open(fd)


class File_Queue:
    """A simple file-based queue implementation for multiprocess communication."""

    def __init__(self) -> None:
        self.directory = tempfile.gettempdir()
        self.filename = os.path.join(self.directory, secrets.token_hex(8))

    def put(self, data: str) -> None:
        """Push a string to the queue."""
        try:
            with open_file(self.filename, "ab") as f:
                lock(f)
                f.seek(0, os.SEEK_END)
                f.write((data + "\x00").encode())
                unlock(f)
        except Exception as e:  # nosec
            print(f"Failed to write to file queue: {self.filename} {data} {e!r}", file=sys.stderr)
            pass

    def get_all(self) -> typing.Set[str]:
        """Pop all unique strings from the queue."""
        try:
            with open_file(self.filename, "r+b") as f:
                lock(f)
                f.seek(0)
                data = f.read().decode()
                f.seek(0)
                f.truncate()
                unlock(f)
            if not data:
                return set()
            return set(data.split("\x00")[:-1])
        except Exception as e:  # nosec
            print(f"Failed to read from file queue: {self.filename} {e!r}", file=sys.stderr)
            return set()
