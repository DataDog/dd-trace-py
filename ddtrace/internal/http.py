from typing import Any
from typing import Optional

from ddtrace.internal.runtime import container


class _CaseInsensitiveHeaders:
    """Minimal case-insensitive header map."""

    def __init__(self, headers: list[tuple[str, str]]) -> None:
        self._data = {k.lower(): v for k, v in headers}

    def get(self, name: str, default: Optional[str] = None) -> Optional[str]:
        return self._data.get(name.lower(), default)

    def __getitem__(self, name: str) -> str:
        return self._data[name.lower()]


class HTTPResponse:
    """Response object returned by :meth:`NativeHTTPConnection.getresponse`.

    Mirrors the subset of ``http.client.HTTPResponse`` that ddtrace internals
    consume: ``status``, ``reason``, ``headers``, ``getheader()``, and ``read()``.
    """

    def __init__(self, native_resp: Any) -> None:
        self._native_resp = native_resp

    def read(self) -> bytes:
        return self._native_resp.body()

    @property
    def status(self) -> int:
        return self._native_resp.status_code

    @property
    def reason(self) -> str:
        return ""

    @property
    def headers(self) -> _CaseInsensitiveHeaders:
        return _CaseInsensitiveHeaders(self._native_resp.headers)

    def getheader(self, name: str, default: Optional[str] = None) -> Optional[str]:
        return self._native_resp.header(name) or default


class NativeHTTPConnection:
    """An http.client-compatible connection backed by the native Rust HTTP client.

    The class shares ``HTTPClient`` instances across calls for the same
    (base_url, timeout_ms) pair so that connection pooling still applies.
    """

    # Class-level cache: (base_url, timeout_ms) -> HTTPClient
    _client_cache: dict[tuple[str, int], Any] = {}

    def __init__(self, base_url: str, timeout: float) -> None:
        self._base_url = base_url
        self._timeout_ms = int(timeout * 1000)
        self._method: Optional[str] = None
        self._path: Optional[str] = None
        self._pending_body: Optional[bytes] = None
        self._pending_headers: list[tuple[str, str]] = []

    def _get_client(self) -> Any:
        from ddtrace.internal.http_client import HTTPClient

        key = (self._base_url, self._timeout_ms)
        if key not in NativeHTTPConnection._client_cache:
            NativeHTTPConnection._client_cache[key] = HTTPClient(
                self._base_url,
                timeout_ms=self._timeout_ms,
                treat_http_errors_as_errors=False,
            )
        return NativeHTTPConnection._client_cache[key]

    def request(self, method: str, url: str, body: Any = None, headers: Any = {}) -> None:
        self._method = method.lower()
        self._path = url
        if isinstance(body, str):
            body = body.encode()
        self._pending_body = body
        _headers: dict[str, str] = dict(headers)
        container.update_headers(_headers)
        self._pending_headers = list(_headers.items())

    def getresponse(self) -> HTTPResponse:
        if self._method is None:
            raise RuntimeError("getresponse() called before request()")
        client = self._get_client()
        req_fn = getattr(client, self._method)
        kwargs: dict[str, Any] = {"headers": self._pending_headers}
        if self._pending_body is not None:
            kwargs["body"] = self._pending_body
        return HTTPResponse(req_fn(self._path, **kwargs))

    def close(self) -> None:
        pass


def _build_base_url(scheme: str, host: str, port: Optional[int], base_path: str) -> str:
    port_part = f":{port}" if port is not None else ""
    path = base_path.rstrip("/") if base_path and base_path != "/" else ""
    return f"{scheme}://{host}{port_part}{path}"


class HTTPConnection(NativeHTTPConnection):
    """Drop-in replacement for httplib.HTTPConnection backed by the native Rust HTTP client.

    Accepts the same (host, port, timeout) constructor signature and the
    ``with_base_path`` factory used throughout ddtrace internals.
    """

    def __init__(self, host: str, port: Optional[int] = None, timeout: float = 2.0, **_: Any) -> None:
        super().__init__(_build_base_url("http", host, port, "/"), timeout)

    @classmethod
    def with_base_path(
        cls, host: str, port: Optional[int] = None, *, base_path: str = "/", timeout: float = 2.0
    ) -> "HTTPConnection":
        inst = cls.__new__(cls)
        NativeHTTPConnection.__init__(inst, _build_base_url("http", host, port, base_path), timeout)
        return inst


class HTTPSConnection(NativeHTTPConnection):
    """Drop-in replacement for httplib.HTTPSConnection backed by the native Rust HTTP client.

    Accepts the same (host, port, timeout) constructor signature and the
    ``with_base_path`` factory used throughout ddtrace internals.
    """

    def __init__(self, host: str, port: Optional[int] = None, timeout: float = 2.0, **_: Any) -> None:
        super().__init__(_build_base_url("https", host, port, "/"), timeout)

    @classmethod
    def with_base_path(
        cls, host: str, port: Optional[int] = None, *, base_path: str = "/", timeout: float = 2.0
    ) -> "HTTPSConnection":
        inst = cls.__new__(cls)
        NativeHTTPConnection.__init__(inst, _build_base_url("https", host, port, base_path), timeout)
        return inst
