"""Deferred WSGI response execution in one isolated context."""

from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
from contextvars import copy_context
import sys
from types import TracebackType
from typing import Any
from typing import ContextManager
from typing import Optional

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)
ExcInfo = tuple[Optional[type[BaseException]], Optional[BaseException], Optional[TracebackType]]


class WSGIResponseIterable:
    """Own a request scope through response exhaustion, failure, or explicit close."""

    def __init__(self, scope: ContextManager[Any]) -> None:
        self._context = copy_context()
        self._scope = scope
        self._response: Optional[Iterable[bytes]] = None
        self._iterator: Optional[Iterator[bytes]] = None
        self._finished = False

    def start(self, application: Callable[[], Iterable[bytes]]) -> "WSGIResponseIterable":
        self._context.run(self._start, application)
        return self

    def _start(self, application: Callable[[], Iterable[bytes]]) -> None:
        self._scope.__enter__()
        try:
            self._response = application()
        except BaseException:
            self._finish(sys.exc_info())
            raise

    def __iter__(self) -> "WSGIResponseIterable":
        return self

    def __next__(self) -> bytes:
        return self._context.run(self._next)

    def _next(self) -> bytes:
        if self._finished:
            raise StopIteration
        try:
            if self._iterator is None:
                if self._response is None:
                    raise RuntimeError("WSGI response has not started")
                self._iterator = iter(self._response)
            return next(self._iterator)
        except StopIteration:
            self._finish((None, None, None))
            raise
        except BaseException:
            self._finish(sys.exc_info())
            raise

    def close(self) -> None:
        self._context.run(self._finish, (None, None, None))

    def _finish(self, exc_info: ExcInfo) -> None:
        if self._finished:
            return
        self._finished = True
        try:
            close = getattr(self._response, "close", None)
            if close is not None:
                try:
                    close()
                except BaseException as close_error:
                    if exc_info[0] is None or not isinstance(close_error, Exception):
                        exc_info = sys.exc_info()
                        raise
                    # A cleanup failure must not hide the application's exception.
                    log.debug("Failed to close WSGI response after application failure", exc_info=True)
        finally:
            try:
                self._scope.__exit__(*exc_info)
            finally:
                self._response = None
                self._iterator = None
