import functools
import socket
from typing import Any
from typing import Callable


_hostname = None


def _cached(func):
    # type: (Callable) -> Callable[[], Any]
    @functools.wraps(func)
    def wrapper():
        global _hostname
        if not _hostname:
            _hostname = func()

        return _hostname

    return wrapper


@_cached
def get_hostname():
    # type: () -> str
    return socket.gethostname()
