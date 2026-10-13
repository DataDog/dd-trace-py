from functools import lru_cache
from http.client import HTTPConnection
from types import TracebackType
from typing import Any
from typing import Optional
from typing import TypeVar
from urllib import parse

from ddtrace.contrib._events.http_client import HttpClientEvents
from ddtrace.contrib._events.http_client import HttpClientResponseEvent
from ddtrace.contrib._events.http_client import HttpClientSendEvent
from ddtrace.internal import core
from ddtrace.internal.constants import _HTTPLIB_NO_TRACE_REQUEST
from ddtrace.internal.wrapping.context import WrappingContext
from ddtrace.internal.wrapping.hooks import try_unwrap_context
from ddtrace.internal.wrapping.hooks import try_wrap_context
from ddtrace.trace import tracer


_patched = False
_REQUEST_STARTED = "context.started." + HttpClientEvents.HTTPLIB_REQUEST.value
ReturnType = TypeVar("ReturnType")


@lru_cache(maxsize=8)
def _host_and_port(url: str) -> tuple[Optional[str], Optional[int]]:
    parsed = parse.urlparse(url)
    return parsed.hostname, parsed.port


def is_internal_connection(connection: Any) -> bool:
    """Whether a connection carries ddtrace's own traffic rather than the application's."""
    if getattr(connection, _HTTPLIB_NO_TRACE_REQUEST, False):
        return True
    agent_url = tracer.agent_trace_url
    if agent_url:
        return (getattr(connection, "host", None), getattr(connection, "port", None)) == _host_and_port(agent_url)
    return False


class HttpClientRequestContext(WrappingContext):
    """Publish request facts at the native method boundary without adding a traceback frame."""

    def __enter__(self) -> "HttpClientRequestContext":
        super().__enter__()
        if not core.has_listeners(_REQUEST_STARTED):
            return self

        arguments = self.__frame__.f_locals
        connection = arguments.get("self")
        if is_internal_connection(connection):
            return self
        body = arguments.get("body")
        ctx = core.context_with_event(
            HttpClientSendEvent(
                request_url=arguments["url"],
                request_method=arguments["method"],
                # An omitted headers argument is http.client's shared default dict, which a subscriber
                # writing to the event must not be able to change for every later request.
                request_headers=arguments.get("headers") or {},
                request_body=lambda: body,
                connection=connection,
            ),
            context_name_override=HttpClientEvents.HTTPLIB_REQUEST.value,
        )
        ctx.__enter__()
        self.set("core_ctx", ctx)
        return self

    def _close_context(
        self,
        exc_type: Optional[type[BaseException]] = None,
        exc_value: Optional[BaseException] = None,
        exc_tb: Optional[TracebackType] = None,
    ) -> None:
        storage = self._storage.get()
        if storage is not None and (ctx := storage.pop("core_ctx", None)) is not None:
            ctx.__exit__(exc_type, exc_value, exc_tb)

    def __return__(self, value: ReturnType) -> ReturnType:
        self._close_context()
        return super().__return__(value)

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        self._close_context(exc_type, exc_value, exc_tb)
        super().__exit__(exc_type, exc_value, exc_tb)


class HttpClientResponseContext(WrappingContext):
    def _connection(self) -> Optional[HTTPConnection]:
        """The connection getresponse was called on, or None for ddtrace's own connections.

        Read lazily: reading the frame costs more than everything else here, and most subscribers
        decide from the response alone. Nothing may raise into getresponse's caller.
        """
        try:
            connection = self.__frame__.f_locals.get("self")
        except Exception:
            return None
        return None if is_internal_connection(connection) else connection

    def __return__(self, response: ReturnType) -> ReturnType:
        if core.has_listeners(HttpClientEvents.HTTPLIB_RESPONSE.value):
            # Dispatch is synchronous, so the accessor is only ever called while the frame is live.
            core.dispatch_event(HttpClientResponseEvent(response=response, get_connection=self._connection))
        return super().__return__(response)


def patch_http_client_events() -> None:
    """Install neutral observation independently of optional httplib spans."""
    global _patched
    if _patched:
        return
    try_wrap_context("http.client", "HTTPConnection.request", HttpClientRequestContext)
    try_wrap_context("http.client", "HTTPConnection.getresponse", HttpClientResponseContext)
    _patched = True


def unpatch_http_client_events() -> None:
    global _patched
    if not _patched:
        return
    try_unwrap_context("http.client", "HTTPConnection.request")
    try_unwrap_context("http.client", "HTTPConnection.getresponse")
    _patched = False
