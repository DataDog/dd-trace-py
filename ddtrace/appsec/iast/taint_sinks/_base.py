from typing import TYPE_CHECKING

from ddtrace import tracer
from ddtrace.internal.logger import get_logger


if TYPE_CHECKING:
    from typing import Any
    from typing import Callable

log = get_logger(__name__)


def inject_span(func):
    # type: (Callable) -> Callable
    """Get the current root Span and attach it to the wrapped function. We need the span to report the vulnerability
    and update the context with the report information.
    """

    def wrapper(wrapped, instance, args, kwargs):
        # type: (Callable, Any, Any, Any) -> Any
        span = tracer.current_root_span()
        if span:
            return func(wrapped, span, instance, args, kwargs)
        log.warning("No root span in the current execution. Skipping IAST Taint sink.")
        return wrapped(*args, **kwargs)

    return wrapper
