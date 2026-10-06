from __future__ import annotations

from dataclasses import dataclass
from http.client import HTTPConnection
from http.client import HTTPSConnection
import json
import ssl
from typing import Any
from urllib.parse import urlparse

from ddtrace.vendor.otel.exporter.http.transport._base import BaseHTTPResult
from ddtrace.vendor.otel.exporter.http.transport._base import BaseHTTPTransport


@dataclass(frozen=True, slots=True)
class StdlibHTTPResult(BaseHTTPResult):
    body: bytes = b""
    response_headers: tuple[tuple[str, str], ...] = ()

    def content(self) -> bytes:
        return self.body

    def headers(self) -> dict[str, str]:
        return dict(self.response_headers)

    def json(self) -> Any:
        return json.loads(self.body)


class StdlibHTTPTransport(BaseHTTPTransport):
    """Persistent stdlib HTTP transport for the vendored OTLP exporter."""

    def __init__(self, *, verify: bool | str = True, cert: str | tuple[str, str] | None = None, **kwargs: Any) -> None:
        if verify is False:
            self._ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            self._ssl_context.check_hostname = False
            self._ssl_context.verify_mode = ssl.CERT_NONE
        else:
            self._ssl_context = ssl.create_default_context(cafile=verify if isinstance(verify, str) else None)
        if isinstance(cert, tuple):
            self._ssl_context.load_cert_chain(cert[0], cert[1])
        elif cert:
            self._ssl_context.load_cert_chain(cert)
        self._connection: HTTPConnection | None = None
        self._origin: tuple[str, str, int] | None = None

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        data: bytes | None = None,
    ) -> BaseHTTPResult:
        parsed = urlparse(url)
        if not parsed.hostname or parsed.scheme not in ("http", "https"):
            return StdlibHTTPResult(error=ValueError(f"Invalid OTLP HTTP endpoint: {url!r}"))

        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        origin = (parsed.scheme, parsed.hostname, port)
        try:
            if self._connection is None or self._origin != origin:
                self.close()
                if parsed.scheme == "https":
                    self._connection = HTTPSConnection(
                        parsed.hostname, port, timeout=timeout, context=self._ssl_context
                    )
                else:
                    self._connection = HTTPConnection(parsed.hostname, port, timeout=timeout)
                self._origin = origin
            else:
                self._connection.timeout = timeout

            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
            self._connection.request(method, path, body=data, headers=headers or {})
            response = self._connection.getresponse()
            return StdlibHTTPResult(
                status_code=response.status,
                reason=response.reason,
                body=response.read(),
                response_headers=tuple(response.getheaders()),
            )
        except Exception as error:
            self.close()
            return StdlibHTTPResult(error=error)

    def is_connection_error(self, exception: Exception | None) -> bool:
        return isinstance(exception, (ConnectionError, OSError))

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
        self._connection = None
        self._origin = None
