import socket

from ddtrace.internal.settings import env


_hostname: str = env.get("DD_HOSTNAME", "")


def get_hostname() -> str:
    global _hostname
    if not _hostname:
        _hostname = socket.gethostname()
    return _hostname


def _reset() -> None:
    global _hostname
    _hostname = ""
