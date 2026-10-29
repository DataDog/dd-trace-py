"""Synchronous request execution shared by web framework integrations."""

from collections.abc import Callable
from typing import Any
from typing import Optional
from typing import Protocol

from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

# Receives the request event, the callback outcome, and any exception the callback raised.
CompleteCallback = Callable[[WebFrameworkRequestEvent, Any, Optional[BaseException]], None]


class ExecuteAdapter(Protocol):
    """Inspects and may replace a callback outcome while the request context is active.

    invoke runs the application and completes its metadata before returning or re-raising.
    kwargs holds the callback's keyword arguments. An adapter that skips or replaces the
    callback owns updating the final metadata through complete.
    """

    def __call__(
        self,
        event: WebFrameworkRequestEvent,
        invoke: Callable[[], Any],
        kwargs: dict[str, Any],
        complete: CompleteCallback,
    ) -> Any: ...


def complete_request_safely(
    complete: CompleteCallback,
    event: WebFrameworkRequestEvent,
    result: Any,
    error: Optional[BaseException],
) -> None:
    """Isolate metadata failures without suppressing process-control exceptions."""
    try:
        complete(event, result, error)
    except Exception:
        log.debug("Failed to complete web framework request metadata", exc_info=True)


def run_web_framework_request(
    callback: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    prepare: Callable[[], WebFrameworkRequestEvent],
    complete: CompleteCallback,
    context_name_override: Optional[str] = None,
    execute: Optional[ExecuteAdapter] = None,
) -> Any:
    """Run a synchronous callback and complete its metadata inside a typed context.

    The optional execute adapter may inspect the callback outcome and replace it while
    the context remains active; see ExecuteAdapter for what it receives.
    Preparation failure bypasses the context and adapter, invoking the application
    once. Returned iterables are neither wrapped nor consumed.
    """
    event: Optional[WebFrameworkRequestEvent]
    try:
        event = prepare()
    except Exception:
        log.debug("Failed to prepare web framework request metadata", exc_info=True)
        event = None
    if event is None:
        return callback(*args, **kwargs)

    def invoke() -> Any:
        result = None
        error: Optional[BaseException] = None
        try:
            result = callback(*args, **kwargs)
            return result
        except BaseException as exc:
            error = exc
            raise
        finally:
            try:
                complete_request_safely(complete, event, result, error)
            finally:
                # Do not retain the application's traceback through this frame.
                error = None

    with core.context_with_event(event, context_name_override=context_name_override):
        return invoke() if execute is None else execute(event, invoke, kwargs, complete)
