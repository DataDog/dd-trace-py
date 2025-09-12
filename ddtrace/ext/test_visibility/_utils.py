from functools import wraps

from ddtrace import config as ddconfig
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def _noop_decorator(func):
    return func


def _catch_and_log_exceptions(func):
    """This decorator is meant to be used around all methods of the Test Visibility classes.
    It accepts an optional parameter to allow it to be used on functions and methods.
    No uncaught errors should ever reach the integration-side, and potentially cause crashes.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:  # noqa: E722
            log.error("Uncaught exception occurred while calling %s", func.__name__, exc_info=True)

    return wrapper


if ddconfig._raise:
    _catch_and_log_decorator = _noop_decorator
