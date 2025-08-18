import os
import socket


_hostname: str = os.getenv("DD_HOSTNAME", "") or ""


def get_hostname() -> str:
    global _hostname
    if not _hostname:
        _hostname = socket.gethostname()
    return _hostname


def _reset() -> None:
    global _hostname
    _hostname = ""
