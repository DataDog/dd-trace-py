import http.client as httplib
import socket
from typing import Any  # noqa:F401

from .http import HTTPConnectionMixin


_GLOBAL_DEFAULT_TIMEOUT = getattr(socket, "_GLOBAL_DEFAULT_TIMEOUT", None)


class UDSHTTPConnection(HTTPConnectionMixin, httplib.HTTPConnection):
    """An HTTP connection established over a Unix Domain Socket."""

    # It's "important" to keep the hostname and port arguments here; while there are not used by the connection
    # mechanism, they are actually used as HTTP headers such as `Host`.
    def __init__(
        self,
        path: str,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super(UDSHTTPConnection, self).__init__(*args, **kwargs)
        self.path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if isinstance(self.timeout, (int, float)):
            sock.settimeout(self.timeout)
        sock.connect(self.path)
        self.sock = sock
