"""Flask response iteration and deferred context completion."""

from collections.abc import Iterable
from collections.abc import Iterator
from contextvars import Context
import sys
from types import TracebackType
from typing import Optional

from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)
ExcInfo = tuple[Optional[type[BaseException]], Optional[BaseException], Optional[TracebackType]]


class FlaskResponseIterable:
    """Signal deferred request completion without creating or finishing spans."""

    def __init__(
        self, response: Iterable[bytes], ctx: core.ExecutionContext[WebFrameworkRequestEvent], context: Context
    ) -> None:
        # Older Flask stream_with_context creates reset tokens during application
        # execution. Iteration and close must run in that exact Context.
        self._context = context
        self._ctx = ctx
        self._response: Optional[Iterable[bytes]] = response
        self._iterator: Optional[Iterator[bytes]] = None
        self._finished = False

    def __iter__(self) -> "FlaskResponseIterable":
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
                self._ctx.dispatch_ended_event(*exc_info)
            finally:
                self._response = None
                self._iterator = None
