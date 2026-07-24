from functools import lru_cache
from http import HTTPStatus
import logging
import socket
from typing import Any
from typing import Optional
from urllib.parse import urlsplit

from ddtrace.internal import forksafe
from ddtrace.internal.native import ConnectionFailedError
from ddtrace.internal.runtime import container


log = logging.getLogger(__name__)


@lru_cache(maxsize=64)
def _build_client(base_url: str, timeout_ms: int) -> Any:
    from ddtrace.internal.http_client import HTTPClient

    return HTTPClient(base_url, timeout_ms=timeout_ms, treat_http_errors_as_errors=False)


forksafe.register(_build_client.cache_clear)


def _diagnose_connection_failure(base_url: str) -> str:
    # DEV: the native client's ConnectionFailedError message only carries the
    # Rust HTTP backend's top-level error Display, not its full `.source()`
    # chain (e.g. the underlying DNS failure) — that detail is discarded
    # before it reaches Python. Resolving the hostname ourselves recovers it
    # for debugging, since DNS failures are otherwise indistinguishable from
    # any other connection failure from this exception alone.
    host = urlsplit(base_url).hostname
    if not host:
        return f"could not parse a host from base_url={base_url!r}"
    try:
        socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        return f"DNS resolution for host {host!r} failed: {e}"
    except Exception as e:
        return f"DNS resolution for host {host!r} raised {e!r}"
    return f"DNS resolution for host {host!r} succeeded; the failure is not name resolution"


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
        try:
            return HTTPStatus(self.status).phrase
        except ValueError:
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
    Instances are built and cached by :func:`_build_client`, an ``lru_cache``-
    wrapped function — thread-safe and bounded (LRU-evicted) for free.
    """

    def __init__(self, base_url: str, timeout: float) -> None:
        self._base_url = base_url
        self._timeout_ms = int(timeout * 1000)
        self._method: Optional[str] = None
        self._path: Optional[str] = None
        self._pending_body: Optional[bytes] = None
        self._pending_headers: list[tuple[str, str]] = []

    def _get_client(self) -> Any:
        return _build_client(self._base_url, self._timeout_ms)

    def request(self, method: str, url: str, body: Any = None, headers: Optional[Any] = None) -> None:
        self._method = method.lower()
        self._path = url
        if isinstance(body, str):
            body = body.encode()
        self._pending_body = body
        _headers: dict[str, str] = dict(headers) if headers else {}
        container.update_headers(_headers)
        self._pending_headers = [(k, str(v)) for k, v in _headers.items()]

    def getresponse(self) -> HTTPResponse:
        if self._method is None:
            raise RuntimeError("getresponse() called before request()")
        client = self._get_client()
        req_fn = getattr(client, self._method)
        kwargs: dict[str, Any] = {"headers": self._pending_headers}
        # get()/delete() don't accept a body kwarg at all, unlike post()/put()/patch().
        if self._pending_body is not None and self._method not in ("get", "delete"):
            kwargs["body"] = self._pending_body
        try:
            return HTTPResponse(req_fn(self._path, **kwargs))
        except ConnectionFailedError:
            log.debug(
                "native HTTP client connection to %s failed: %s",
                self._base_url,
                _diagnose_connection_failure(self._base_url),
            )
            raise

    def close(self) -> None:
        pass


class HTTPConnection(NativeHTTPConnection):
    """HTTP/HTTPS/Unix connection backed by the native Rust HTTP client.

    Expects a full base URL (``scheme://host[:port]``).  The Rust client
    dispatches to HTTP, HTTPS, or Unix domain socket based on the scheme.
    """

    def __init__(self, url: str, timeout: float = 2.0) -> None:
        super().__init__(url, timeout)
