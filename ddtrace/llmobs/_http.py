import os
import ssl
from urllib.parse import urlparse

from ddtrace.internal.utils.http import DEFAULT_TIMEOUT
from ddtrace.internal.utils.http import ConnectionType
from ddtrace.internal.utils.http import HTTPConnection
from ddtrace.internal.utils.http import HTTPSConnection
from ddtrace.internal.utils.http import verify_url


class ProxiedHTTPConnection(HTTPConnection):
    """HTTPConnection that connects through an HTTP proxy."""

    def __init__(self, host, port=None, **kwargs):
        proxy_url = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
        if proxy_url:
            proxy = urlparse(proxy_url)
            self.proxy_host = proxy.hostname
            self.proxy_port = proxy.port or 8080
            self.proxy_auth = None
            if proxy.username and proxy.password:
                import base64

                creds = f"{proxy.username}:{proxy.password}".encode()
                self.proxy_auth = base64.b64encode(creds).decode()
            super().__init__(self.proxy_host, self.proxy_port, **kwargs)
            self._tunnel_host = host
            self._tunnel_port = port or 80
        else:
            super().__init__(host, port, **kwargs)
            self.proxy_host = self.proxy_port = self.proxy_auth = None

    def connect(self):
        super().connect()
        if hasattr(self, "_tunnel_host"):
            # If HTTP proxy, tell it where to connect
            self.send(f"CONNECT {self._tunnel_host}:{self._tunnel_port} HTTP/1.1\r\n".encode())
            self.send(f"Host: {self._tunnel_host}:{self._tunnel_port}\r\n".encode())
            if self.proxy_auth:
                self.send(f"Proxy-Authorization: Basic {self.proxy_auth}\r\n".encode())
            self.send(b"\r\n")
            response = self.response_class(self.sock, method="CONNECT")
            response.begin()
            if response.status != 200:
                raise OSError(f"Proxy CONNECT failed: {response.status} {response.reason}")


class ProxiedHTTPSConnection(HTTPSConnection):
    """HTTPSConnection that connects through an HTTP or HTTPS proxy."""

    def __init__(self, host, port=None, context=None, **kwargs):
        super().__init__(host, port, **kwargs)
        self.context = context or ssl.create_default_context()
        self.use_tls = True

    def connect(self):
        super().connect()
        # Wrap socket in TLS *after* CONNECT
        if hasattr(self, "_tunnel_host"):
            self.sock = self.context.wrap_socket(self.sock, server_hostname=self._tunnel_host)


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
