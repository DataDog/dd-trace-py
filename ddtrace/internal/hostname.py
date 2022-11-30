import socket
from typing import Optional


_hostname = None  # type: Optional[str]


def get_hostname():
    # type: () -> str
    global _hostname
    if not _hostname:
        _hostname = socket.gethostname()
    return _hostname


def _reset():
    global _hostname
    _hostname = None
