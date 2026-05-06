from functools import wraps
import logging
import typing as t

from ddtrace.internal.settings import env
from ddtrace.testing.internal.utils import asbool


testing_logger = logging.getLogger("ddtrace.testing")

F = t.TypeVar("F", bound=t.Callable[..., t.Any])


class _SafeStreamHandler(logging.StreamHandler):
    """StreamHandler that silences I/O errors on closed streams.

    Background threads (e.g. the periodic writer flush) may attempt to log
    after the interpreter has started tearing down and ``sys.stderr`` is
    already closed.  The default ``StreamHandler`` lets the resulting
    ``ValueError`` propagate to ``handleError``, which prints a noisy
    ``--- Logging error ---`` traceback.  That output can pollute
    subprocess stdout/stderr and cause otherwise-passing tests to fail.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except ValueError:
            # Stream was closed during interpreter shutdown — nothing we can do.
            pass


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
