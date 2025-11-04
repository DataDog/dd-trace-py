import base64
import os
import ssl
from typing import List
from typing import Optional
from typing import Tuple
from urllib.parse import urlparse

from ddtrace.internal.http import HTTPConnection
from ddtrace.internal.http import HTTPSConnection
from ddtrace.internal.utils.http import DEFAULT_TIMEOUT
from ddtrace.internal.utils.http import ConnectionType
from ddtrace.internal.utils.http import verify_url


class ProxyConnectionMixin:
    """
    Mixin for HTTP connections that adds proxy support.

    Handles proxy detection from environment variables, authentication,
    and CONNECT method for tunneling.
    """

    proxy_host: Optional[str]
    proxy_port: Optional[int]
    proxy_auth: Optional[str]
    _tunnel_host: str
    _tunnel_port: int

    def _get_proxy_config(
        self, host: str, port: Optional[int], default_port: int, proxy_env_vars: List[str]
    ) -> Tuple[Optional[str], Optional[int], str, int]:
        """
        Get proxy configuration from environment variables.

        Args:
            host: Target host to connect to
            port: Target port to connect to (or None for default)
            default_port: Default port if none specified
            proxy_env_vars: List of environment variable names to check (in order of precedence)

        Returns:
            Tuple of (proxy_host, proxy_port, tunnel_host, tunnel_port)
            If no proxy, returns (None, None, host, port)
        """
        proxy_url = None
        for env_var in proxy_env_vars:
            proxy_url = os.getenv(env_var)
            if proxy_url:
                break

        tunnel_port = port or default_port

        if proxy_url:
            proxy = urlparse(proxy_url)
            proxy_host = proxy.hostname
            # Default to 3128 (Squid's default port, de facto standard for HTTP proxies)
            proxy_port = proxy.port or 3128

            # Store proxy auth if provided
            self.proxy_auth = None
            if proxy.username and proxy.password:
                creds = f"{proxy.username}:{proxy.password}".encode()
                self.proxy_auth = base64.b64encode(creds).decode()

            return proxy_host, proxy_port, host, tunnel_port
        else:
            self.proxy_auth = None
            return None, None, host, tunnel_port

    def _send_connect_request(self) -> None:
        """Send HTTP CONNECT request to proxy for tunneling."""
        if not hasattr(self, "_tunnel_host"):
            return

        # Build and send CONNECT request
        connect_req = (
            f"CONNECT {self._tunnel_host}:{self._tunnel_port} HTTP/1.1\r\n"
            f"Host: {self._tunnel_host}:{self._tunnel_port}\r\n"
        )
        if self.proxy_auth:
            connect_req += f"Proxy-Authorization: Basic {self.proxy_auth}\r\n"
        connect_req += "\r\n"

        self.sock.sendall(connect_req.encode("latin-1"))

        # Use http.client.HTTPResponse to properly read the proxy's HTTP response
        # We need to wrap the socket in a file-like object for HTTPResponse
        import http.client

        # Create an HTTPResponse to parse the proxy's response
        response = http.client.HTTPResponse(self.sock)
        # We need to manually begin the response (normally done by HTTPConnection)
        response.begin()

        if response.status != 200:
            raise OSError(f"Proxy CONNECT failed: {response.status} {response.reason}")


class ProxiedHTTPConnection(ProxyConnectionMixin, HTTPConnection):
    """HTTPConnection that connects through an HTTP proxy."""

    def __init__(self, host: str, port: Optional[int] = None, **kwargs) -> None:
        # For HTTP through proxy, we don't need tunneling - just connect directly
        # The proxy is typically only used with CONNECT for HTTPS tunneling
        # For plain HTTP through a proxy, requests go directly to the proxy
        super().__init__(host, port, **kwargs)

    def connect(self) -> None:
        super().connect()


class ProxiedHTTPSConnection(ProxyConnectionMixin, HTTPSConnection):
    """HTTPSConnection that connects through an HTTP or HTTPS proxy."""

    context: ssl.SSLContext
    use_tls: bool

    def __init__(
        self, host: str, port: Optional[int] = None, context: Optional[ssl.SSLContext] = None, **kwargs
    ) -> None:
        # Determine if we're using a proxy and what the target host/port are
        proxy_host, proxy_port, tunnel_host, tunnel_port = self._get_proxy_config(
            host, port, default_port=443, proxy_env_vars=["HTTPS_PROXY", "HTTP_PROXY"]
        )

        # Initialize the connection to the proxy (or directly to target if no proxy)
        super().__init__(proxy_host or host, proxy_port or port, **kwargs)

        # Store tunnel information AFTER super().__init__() since it resets these
        if proxy_host:
            self._tunnel_host = tunnel_host
            self._tunnel_port = tunnel_port

        self.context = context or ssl.create_default_context()
        self.use_tls = True

    def connect(self) -> None:
        # When using a proxy, we need to establish a plain TCP connection first,
        # send CONNECT, then wrap with SSL. When not using a proxy, use normal HTTPS.
        if hasattr(self, "_tunnel_host"):
            # Connect to proxy with plain TCP socket
            import socket
            self.sock = socket.create_connection((self.host, self.port), self.timeout)
            self._send_connect_request()
            # Now wrap the socket for HTTPS to the target server
            self.sock = self.context.wrap_socket(self.sock, server_hostname=self._tunnel_host)
        else:
            # No proxy, use standard HTTPS connection
            super().connect()


def get_connection(url: str, timeout: float = DEFAULT_TIMEOUT) -> ConnectionType:
    """Return an HTTP connection to the given URL."""
    parsed = verify_url(url)
    hostname = parsed.hostname or ""
    path = parsed.path or "/"

    from ddtrace.internal.uds import UDSHTTPConnection

    if parsed.scheme == "https":
        return ProxiedHTTPSConnection.with_base_path(hostname, parsed.port, base_path=path, timeout=timeout)
    elif parsed.scheme == "http":
        return ProxiedHTTPConnection.with_base_path(hostname, parsed.port, base_path=path, timeout=timeout)
    elif parsed.scheme == "unix":
        return UDSHTTPConnection(path, hostname, parsed.port, timeout=timeout)

    raise ValueError("Unsupported protocol '%s'" % parsed.scheme)
