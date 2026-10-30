"""Synchronous request execution shared by web framework integrations."""

from collections.abc import Callable
from typing import Any
from typing import Optional

from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

# Receives the request event, the callback outcome, and any exception the callback raised.
CompleteCallback = Callable[[WebFrameworkRequestEvent, Any, Optional[BaseException]], None]


def run_web_framework_request(
    callback: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    prepare: Callable[[], WebFrameworkRequestEvent],
    complete: CompleteCallback,
    context_name_override: Optional[str] = None,
) -> Any:
    """Run a synchronous callback and complete its metadata inside a typed context.

    Preparation failure bypasses the context and invokes the application once.
    Returned iterables are neither wrapped nor consumed.
    """
    event: Optional[WebFrameworkRequestEvent]
    try:
        event = prepare()
    except Exception:
        log.debug("Failed to prepare web framework request metadata", exc_info=True)
        event = None
    if event is None:
        return callback(*args, **kwargs)

    with core.context_with_event(event, context_name_override=context_name_override):
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
                complete(event, result, error)
            except Exception:
                log.debug("Failed to complete web framework request metadata", exc_info=True)
            finally:
                # Do not retain the application's traceback through this frame.
                error = None
