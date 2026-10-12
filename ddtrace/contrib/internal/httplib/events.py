from types import TracebackType
from typing import Optional
from typing import TypeVar

from ddtrace.contrib._events.http_client import HttpClientEvents
from ddtrace.contrib._events.http_client import HttpClientResponseEvent
from ddtrace.contrib._events.http_client import HttpClientSendEvent
from ddtrace.internal import core
from ddtrace.internal.wrapping.context import WrappingContext
from ddtrace.internal.wrapping.hooks import try_unwrap_context
from ddtrace.internal.wrapping.hooks import try_wrap_context


_patched = False
_REQUEST_STARTED = "context.started." + HttpClientEvents.HTTPLIB_REQUEST.value
ReturnType = TypeVar("ReturnType")


class HttpClientRequestContext(WrappingContext):
    """Publish request facts at the native method boundary without adding a traceback frame."""

    def __enter__(self) -> "HttpClientRequestContext":
        super().__enter__()
        if not core.has_listeners(_REQUEST_STARTED):
            return self

        arguments = self.__frame__.f_locals
        body = arguments.get("body")
        ctx = core.context_with_event(
            HttpClientSendEvent(
                request_url=arguments["url"],
                request_method=arguments["method"],
                request_headers=arguments.get("headers", {}),
                request_body=lambda: body,
                connection=arguments.get("self"),
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
    def __return__(self, response: ReturnType) -> ReturnType:
        if core.has_listeners(HttpClientEvents.HTTPLIB_RESPONSE.value):
            core.dispatch_event(HttpClientResponseEvent(response=response))
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
