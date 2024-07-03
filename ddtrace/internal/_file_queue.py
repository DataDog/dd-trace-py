import os
import os.path
import secrets
import tempfile
import typing

from ddtrace.internal._unpatched import unpatched_open


MAX_FILE_SIZE = 8192

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

    def lock(f):
        # You need to seek to the beginning of the file before locking it
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_RLCK, MAX_FILE_SIZE)

    def unlock(f):
        # You need to seek to the same position of the file when you locked before unlocking it
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, MAX_FILE_SIZE)

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
                if f.tell() < MAX_FILE_SIZE:
                    f.write((data + "\x00").encode())
                unlock(f)
        except Exception:  # nosec
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
            if data:
                return set(data.split("\x00")[:-1])
        except Exception:  # nosec
            pass
        return set()
