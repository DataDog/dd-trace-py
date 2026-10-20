"""WSGI execution for the shared HTTP lifecycle; no framework imports."""

from collections.abc import Callable
from collections.abc import Iterable
from contextvars import copy_context
from dataclasses import dataclass
from http import HTTPStatus
import sys
from typing import Any
from typing import Optional
from typing import Protocol
from typing import TypeVar
from urllib.parse import parse_qsl
from wsgiref.util import request_uri

from ddtrace.contrib._events.http import HttpBlockingResponse
from ddtrace.contrib._events.http import HttpRequestData
from ddtrace.contrib._events.http import HttpResponseData
from ddtrace.contrib.internal.http_server import HttpLifecycle
from ddtrace.contrib.internal.http_server import HttpLifecycleConfig
from ddtrace.contrib.internal.http_server import has_http_listeners
from ddtrace.trace import tracer


_REQUEST_SCOPE = "datadog.http.request"
_Result = TypeVar("_Result")
# WSGI adapters own framing and cannot control hop-by-hop transport headers.
_TRANSPORT_HEADERS = frozenset(
    (
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",  # codespell:ignore te
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-length",
    )
)


def _valid_wsgi_block(block: HttpBlockingResponse) -> bool:
    return all(name.lower() not in _TRANSPORT_HEADERS for name, _ in block.headers)


class WsgiBlockingResponse(Protocol):
    @property
    def status(self) -> str: ...

    @property
    def status_code(self) -> int: ...

    @property
    def headerlist(self) -> list[tuple[str, str]]: ...

    @property
    def body(self) -> bytes: ...


def request_data_from_environ(environ: dict[str, Any]) -> HttpRequestData:
    """Extract standard WSGI values without consuming or caching request input."""
    headers = []
    for name, value in environ.items():
        if name.startswith("HTTP_"):
            headers.append((name[5:].replace("_", "-").title(), value))
        elif name in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            headers.append((name.replace("_", "-").title(), value))
    query = environ.get("QUERY_STRING", "")
    return HttpRequestData(
        method=environ["REQUEST_METHOD"],
        url=request_uri(environ),
        headers=tuple(headers),
        query_string=query,
        query_parameters=tuple(parse_qsl(query, keep_blank_values=True)),
    )


@dataclass
class _BlockingResponse:
    status_code: int
    headerlist: list[tuple[str, str]]
    body: bytes

    @property
    def status(self) -> str:
        try:
            reason = HTTPStatus(self.status_code).phrase
        except ValueError:
            reason = "Unknown"
        return f"{self.status_code} {reason}"


def blocking_response_from_data(block: HttpBlockingResponse) -> WsgiBlockingResponse:
    """Supply transport framing without requiring a framework response class."""
    headers = list(block.headers)
    if block.status_code not in (204, 304):
        headers.append(("Content-Length", str(len(block.body))))
    return _BlockingResponse(block.status_code, headers, block.body)


def current_http_lifecycle(environ: dict[str, Any]) -> Optional[HttpLifecycle]:
    request = environ.get(_REQUEST_SCOPE)
    return request.lifecycle if request is not None else None


class WsgiLifecycleDriver:
    """Wrap WSGI directly; framework-specific extraction and conversion are optional."""

    def __init__(
        self,
        *,
        get_config: Callable[[], Optional[HttpLifecycleConfig]],
        request_data: Callable[[dict[str, Any]], HttpRequestData] = request_data_from_environ,
        blocking_response: Callable[[HttpBlockingResponse], WsgiBlockingResponse] = blocking_response_from_data,
    ) -> None:
        self.request_data = request_data
        self.blocking_response = blocking_response
        self.get_config = get_config

    def current(self, environ: dict[str, Any]) -> Optional[HttpLifecycle]:
        request = environ.get(_REQUEST_SCOPE)
        return request.lifecycle if request is not None and request.driver is self else None

    def wrap(self, application: Callable[..., Any]) -> Callable[..., Any]:
        def traced_wsgi(environ: dict[str, Any], start_response: Callable[..., Any]) -> Any:
            config = self.get_config()
            if config is None or not has_http_listeners():
                return application(environ, start_response)
            lifecycle = HttpLifecycle(config, self.request_data(environ), validate_block=_valid_wsgi_block)
            request = _WsgiRequest(self, lifecycle)
            return request.call(request.run, application, environ, start_response)

        return traced_wsgi


class _WsgiRequest:
    def __init__(self, driver: WsgiLifecycleDriver, lifecycle: HttpLifecycle) -> None:
        self.driver = driver
        self.lifecycle = lifecycle
        self.context = copy_context()
        self.provider = tracer.context_provider

    def call(self, function: Callable[..., _Result], *args: Any) -> _Result:
        previous = self.provider.active()
        try:
            return self.context.run(self._call, function, args)
        finally:
            # Context.run restores contextvars, but activation subscribers (such as
            # profiling) also need notification when the request yields to its caller.
            self.provider.activate(previous)

    def _call(self, function: Callable[..., _Result], args: tuple[Any, ...]) -> _Result:
        self.provider.activate(self.provider.active())
        return function(*args)

    def run(self, application: Callable[..., Any], environ: dict[str, Any], start_response: Callable[..., Any]) -> Any:
        lifecycle = self.lifecycle
        previous = environ.get(_REQUEST_SCOPE)
        environ[_REQUEST_SCOPE] = self
        replacement: Optional[WsgiBlockingResponse] = None

        def response_ready(status: str, headers: list[tuple[str, str]], exc_info: Any = None) -> Any:
            nonlocal replacement
            replacement = None
            if exc_info:
                lifecycle.record_exception(exc_info)
            block = lifecycle.prepare_response(
                HttpResponseData(status_code=int(status.split(" ", 1)[0]), headers=tuple(headers))
            )
            if block is not None:
                replacement = self.driver.blocking_response(block)
                status, headers = replacement.status, replacement.headerlist
            lifecycle.delivery_attempted(
                HttpResponseData(status_code=int(status.split(" ", 1)[0]), headers=tuple(headers))
            )
            result = start_response(status, headers, exc_info)
            lifecycle.response_committed()
            return result

        try:
            block = lifecycle.start()
            if block is not None:
                blocked = self.driver.blocking_response(block)
                body = [blocked.body]
                response_ready(blocked.status, blocked.headerlist)
            else:
                body = application(environ, response_ready)
            if replacement is not None:
                close = getattr(body, "close", None)
                if close is not None:
                    close()
                body = [replacement.body]
            if lifecycle.request_data.method == "HEAD":
                close = getattr(body, "close", None)
                if close is not None:
                    close()
                body = []
            return _ResponseIterator(body, self)
        except BaseException:
            lifecycle.record_exception(sys.exc_info())
            lifecycle.finish()
            raise
        finally:
            if previous is None:
                environ.pop(_REQUEST_SCOPE, None)
            else:
                environ[_REQUEST_SCOPE] = previous


class _ResponseIterator:
    """Keep the request active only while producing or closing its WSGI body."""

    def __init__(self, body: Iterable[bytes], request: _WsgiRequest) -> None:
        self.body = body
        self.iterator = iter(body)
        self.request = request
        self.closed = False

    def __iter__(self) -> "_ResponseIterator":
        return self

    def __next__(self) -> bytes:
        return self.request.call(self._next)

    def _next(self) -> bytes:
        if self.request.lifecycle.completed:
            raise StopIteration
        try:
            return next(self.iterator)
        except StopIteration:
            self.request.lifecycle.finish()
            raise
        except BaseException:
            self.request.lifecycle.record_exception(sys.exc_info())
            self.request.lifecycle.finish()
            raise

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.request.call(self._close)

    def _close(self) -> None:
        try:
            close = getattr(self.body, "close", None)
            if close is not None:
                close()
        except BaseException:
            self.request.lifecycle.record_exception(sys.exc_info())
            raise
        finally:
            self.request.lifecycle.finish()
