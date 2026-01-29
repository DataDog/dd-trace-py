import logging
import os
import typing as t

from ddtrace.internal.logger import catch_and_log_exceptions as _catch_and_log
from ddtrace.testing.internal.utils import asbool


testing_logger = logging.getLogger("ddtrace.testing")

F = t.TypeVar("F", bound=t.Callable[..., t.Any])


def setup_logging() -> None:
    testing_logger.propagate = False

    debug_enabled = asbool(os.getenv("DD_TEST_DEBUG")) or asbool(os.getenv("DD_TRACE_DEBUG"))

    log_level = logging.DEBUG if debug_enabled else logging.INFO
    testing_logger.setLevel(log_level)

    for handler in list(testing_logger.handlers):
        testing_logger.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("[Datadog Test Optimization] %(levelname)-8s %(name)s:%(filename)s:%(lineno)d %(message)s")
    )
    testing_logger.addHandler(handler)


catch_and_log_exceptions = _catch_and_log(testing_logger, None)
