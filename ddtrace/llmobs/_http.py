import base64
import os
import ssl
from typing import Optional
from typing import Tuple
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
            proxy_host, proxy_port, tunnel_host, tunnel_port = self._get_proxy_config(
                os.environ["HTTPS_PROXY"], host, port, default_port=443
            )
            super().__init__(proxy_host, proxy_port, **kwargs)
            self.set_tunnel(tunnel_host, tunnel_port)
        else:
            super().__init__(host, port, **kwargs)

    def _get_proxy_config(
        self, proxy_url: str, host: str, port: Optional[int], default_port: int
    ) -> Tuple[str, int, str, int]:
        """Get proxy configuration from environment variables."""
        tunnel_port = port or default_port
        proxy = urlparse(proxy_url)
        proxy_host = proxy.hostname

        # Default to 3128 (Squid's default port, de facto standard for HTTP proxies)
        proxy_port = proxy.port or 3128

        self.proxy_auth = None
        if proxy.username and proxy.password:
            creds = f"{proxy.username}:{proxy.password}".encode()
            self.proxy_auth = base64.b64encode(creds).decode()

        return proxy_host, proxy_port, host, tunnel_port


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
