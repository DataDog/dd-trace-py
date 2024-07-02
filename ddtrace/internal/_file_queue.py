import os
import os.path
import tempfile
import typing


try:
    # Unix based file locking
    # Availability: Unix, not Emscripten, not WASI.
    import fcntl

    def lock(f):
        fcntl.lockf(f, fcntl.LOCK_EX)

    def unlock(f):
        fcntl.lockf(f, fcntl.LOCK_UN)

except ModuleNotFoundError:
    # Availability:Windows file locking
    import msvcrt

    def size(f):
        return os.path.getsize(os.path.realpath(f.name))

    def lock(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_RLCK, size(f))

    def unlock(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, size(f))


class File_Queue:
    """A simple file-based queue implementation for multiprocess communication."""

    def __init__(self) -> None:
        self.directory = tempfile.gettempdir()
        self.filename = os.path.join(self.directory, "file_queue")

    def put(self, data: str) -> None:
        """Push a string to the queue."""
        try:
            with open(self.filename, "a") as f:
                lock(f)
                f.write(data + "\x00")
                unlock(f)
        except Exception:  # nosec
            pass

    def get_all(self) -> typing.Set[str]:
        """Pop all unique strings from the queue."""
        try:
            with open(self.filename, "r+") as f:
                lock(f)
                data = f.read()
                os.unlink(self.filename)
                unlock(f)
            if not data:
                return set()
            return set(data.split("\x00")[:-1])
        except Exception:  # nosec
            return set()
