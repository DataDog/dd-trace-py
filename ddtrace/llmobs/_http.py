import os
import ssl
from typing import Optional
from urllib.parse import urlparse

from ddtrace.internal.http import HTTPConnection
from ddtrace.internal.http import HTTPSConnection
from ddtrace.internal.uds import UDSHTTPConnection
from ddtrace.internal.utils.http import DEFAULT_TIMEOUT
from ddtrace.internal.utils.http import ConnectionType
from ddtrace.internal.utils.http import verify_url


class ProxiedHTTPSConnection(HTTPSConnection):
    """
    The built-in http.client in Python doesn't respect HTTPS_PROXY (even tho other clients like requests and curl do).

    This implementation simply extends the client with support for basic proxies.
    """

    def __init__(
        self, host: str, port: Optional[int] = None, context: Optional[ssl.SSLContext] = None, **kwargs
    ) -> None:
        if "HTTPS_PROXY" in os.environ:
            tunnel_port = port or 443
            proxy = urlparse(os.environ["HTTPS_PROXY"])
            proxy_host = proxy.hostname or ""
            # Default to 3128 (Squid's default port, de facto standard for HTTP proxies)
            proxy_port = proxy.port or 3128
            super().__init__(proxy_host, proxy_port, **kwargs)
            self.set_tunnel(host, tunnel_port)
        else:
            super().__init__(host, port, **kwargs)


def get_connection(url: str, timeout: float = DEFAULT_TIMEOUT) -> ConnectionType:
    """Return an HTTP connection to the given URL."""
    parsed = verify_url(url)
    hostname = parsed.hostname or ""
    path = parsed.path or "/"

    if parsed.scheme == "https":
        return ProxiedHTTPSConnection.with_base_path(hostname, parsed.port, base_path=path, timeout=timeout)
    elif parsed.scheme == "http":
        return HTTPConnection.with_base_path(hostname, parsed.port, base_path=path, timeout=timeout)
    elif parsed.scheme == "unix":
        return UDSHTTPConnection(path, hostname, parsed.port, timeout=timeout)

    raise ValueError("Unsupported protocol '%s'" % parsed.scheme)
