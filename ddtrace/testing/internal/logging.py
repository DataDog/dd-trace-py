from functools import wraps
import logging
import sys
import typing as t

from ddtrace.internal.settings import env
from ddtrace.testing.internal.utils import asbool


testing_logger = logging.getLogger("ddtrace.testing")

F = t.TypeVar("F", bound=t.Callable[..., t.Any])


class _SafeStreamHandler(logging.StreamHandler):
    """StreamHandler that silences I/O errors on closed streams.

    Background threads (e.g. the periodic writer flush) may attempt to log
    after the interpreter has started tearing down and ``sys.stderr`` is
    already closed.  The base ``StreamHandler.emit()`` catches the resulting
    ``ValueError`` internally and delegates to ``handleError()``, which
    prints a noisy ``--- Logging error ---`` traceback to stderr.  That
    output can pollute subprocess stdout/stderr and cause otherwise-passing
    tests to fail.

    We override ``handleError`` so that closed-stream errors are silently
    discarded instead of producing the traceback.
    """

    def handleError(self, record: logging.LogRecord) -> None:
        # If the current exception is a ValueError (I/O on closed file),
        # swallow it — the process is shutting down and there's nowhere
        # useful to write.  For any other error, fall back to the default
        # behaviour so real logging misconfigurations remain visible.
        _, exc, _ = sys.exc_info()
        if isinstance(exc, ValueError):
            return
        super().handleError(record)


def setup_logging() -> None:
    testing_logger.propagate = False

    debug_enabled = asbool(env.get("DD_TEST_DEBUG")) or asbool(env.get("DD_TRACE_DEBUG"))

    log_level = logging.DEBUG if debug_enabled else logging.INFO
    testing_logger.setLevel(log_level)

    for handler in list(testing_logger.handlers):
        testing_logger.removeHandler(handler)

    handler = _SafeStreamHandler()
    handler.setFormatter(
        logging.Formatter("[Datadog Test Optimization] %(levelname)-8s %(name)s:%(filename)s:%(lineno)d %(message)s")
    )
    testing_logger.addHandler(handler)


def catch_and_log_exceptions() -> t.Callable[[F], F]:
    def decorator(f: F) -> F:
        @wraps(f)
        def wrapper(*args: t.Any, **kwargs: t.Any) -> t.Any:
            try:
                return f(*args, **kwargs)
            except Exception:
                testing_logger.exception("Error while calling %s", f.__name__)
                return None

        return t.cast(F, wrapper)

    return decorator
