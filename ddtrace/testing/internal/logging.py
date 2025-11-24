from functools import wraps
import logging
import os
import typing as t

from ddtestpy.internal.utils import asbool


ddtestpy_logger = logging.getLogger("ddtestpy")

F = t.TypeVar("F", bound=t.Callable[..., t.Any])


def setup_logging() -> None:
    ddtestpy_logger.propagate = False

    log_level = logging.DEBUG if asbool(os.getenv("DD_TEST_DEBUG")) else logging.INFO
    ddtestpy_logger.setLevel(log_level)

    for handler in list(ddtestpy_logger.handlers):
        ddtestpy_logger.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("[Datadog Test Optimization] %(levelname)-8s %(name)s:%(filename)s:%(lineno)d %(message)s")
    )
    ddtestpy_logger.addHandler(handler)


def catch_and_log_exceptions() -> t.Callable[[F], F]:
    def decorator(f: F) -> F:
        @wraps(f)
        def wrapper(*args: t.Any, **kwargs: t.Any) -> t.Any:
            try:
                return f(*args, **kwargs)
            except Exception:
                ddtestpy_logger.exception("Error while calling %s", f.__name__)
                return None

        return t.cast(F, wrapper)

    return decorator
