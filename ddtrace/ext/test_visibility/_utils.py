from ddtrace import config as ddconfig
from ddtrace.internal.logger import catch_and_log_exceptions
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def _noop_decorator(func):
    return func


if ddconfig._raise:
    _catch_and_log_exceptions = _noop_decorator
else:
    import logging

    _catch_and_log_exceptions = catch_and_log_exceptions(log, logging.ERROR)
