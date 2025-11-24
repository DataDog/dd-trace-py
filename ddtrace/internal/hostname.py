import socket

from ddtrace.settings._env import get_env as _get_env


_hostname = _get_env("DD_HOSTNAME", "")  # type: str


def get_hostname():
    # type: () -> str
    global _hostname
    if not _hostname:
        _hostname = socket.gethostname()
    return _hostname


def _reset():
    global _hostname
    _hostname = ""
