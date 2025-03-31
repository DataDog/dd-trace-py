"""
Logging utilities for internal use.
Usage:
    import ddtrace.internal.logger as logger
    ddlog = logger.get_logger(__name__)

    # Otherwise default is set to 1 minute or DD_TRACE_LOGGING_RATE
    logger.set_tag_rate_limit("waf::init", logger.HOUR)

    # "product" is required, but other keys are optional as well as kwargs exc_info and stack_info
    # supported keys are
    # product: product or integration name. Required
    # more_info : more information to be logged after the main tag. Default is empty string
    # stack_limit: limit the stack trace to this depth for stack_info. Default is 0 (0 is no limit)
    # exec_limit: limit the stack trace to this depth for exec_info. Default is 1, only the top level (0 is no limit)

    info = # format the info string
    ddlog.debug('waf::init', extra={"product": "appsec", "stack_limit": 4, "more_info": info}, stack_info=True)
    # This will log the message only once per hour, counting the number of skipped messages, using "waf::init" as the
    # tag to keep track of the rate limit
    # Different log levels can be used, the rate limit is shared between all invocations and all levels for the same tag

    # example result
    DEBUG appsec::waf::init[some more info] 1 additional messages skipped
    (followed by the 4 first levels of the stack trace)

    Legacy support:
    if extra is not used or product is absent, the log will be treated as legacy and will be logged as is using
    filename and line number of the log call

"""

import collections
import logging
import os
import time
import traceback
from typing import DefaultDict
from typing import Tuple
from typing import Union


logging.basicConfig()

SECOND = 1
MINUTE = 60 * SECOND
HOUR = 60 * MINUTE
DAY = 24 * HOUR


def get_logger(name: str) -> logging.Logger:
    """
    Retrieve or create a ``Logger`` instance with consistent behavior for internal use.

    Configure all loggers with a rate limiter filter to prevent excessive logging.

    """
    logger = logging.getLogger(name)
    # addFilter will only add the filter if it is not already present
    logger.addFilter(log_filter)
    logger.propagate = True
    return logger


_RATE_LIMITS = {}


def set_tag_rate_limit(tag: str, rate: int) -> None:
    """
    Set the rate limit for a specific tag.

    """
    _RATE_LIMITS[tag] = rate


# Class used for keeping track of a log lines current time bucket and the number of log lines skipped
class LoggingBucket:
    def __init__(self, bucket: float, skipped: int):
        self.bucket = bucket
        self.skipped = skipped

    def __repr__(self):
        return f"LoggingBucket({self.bucket}, {self.skipped})"

    def is_sampled(self, record: logging.LogRecord, rate: float) -> bool:
        """
        Determine if the log line should be sampled based on the rate limit.
        """
        current = time.monotonic()
        if current - self.bucket >= rate:
            self.bucket = current
            record.skipped = self.skipped
            self.skipped = 0
            return True
        self.skipped += 1
        return False


# Dict to keep track of the current time bucket per name/level/pathname/lineno

_MINF = float("-inf")

key_type = Union[Tuple[str, int], str]
_buckets: DefaultDict[key_type, LoggingBucket] = collections.defaultdict(lambda: LoggingBucket(_MINF, 0))

# Allow 1 log record per name/level/pathname/lineno every 60 seconds by default
# Allow configuring via `DD_TRACE_LOGGING_RATE`
# DEV: `DD_TRACE_LOGGING_RATE=0` means to disable all rate limiting
_rate_limit = int(os.getenv("DD_TRACE_LOGGING_RATE", default=60))


def log_filter(record: logging.LogRecord) -> bool:
    """
    Function used to determine if a log record should be outputted or not (True = output, False = skip).

    This function will:
      - Rate limit log records based on the logger name, record level, filename, and line number
    """
    logger = logging.getLogger(record.name)
    # If rate limiting has been disabled (`DD_TRACE_LOGGING_RATE=0`) then apply no rate limit
    # If the logger is set to debug, then do not apply any limits to any log
    if not _rate_limit or logger.getEffectiveLevel() == logging.DEBUG:
        return True
    # Allow 1 log record by pathname/lineno every X seconds or message/levelno for product logs
    # This way each unique log message can get logged at least once per time period
    if hasattr(record, "product"):
        key: key_type = record.msg
    else:
        key = (record.pathname, record.lineno)
    # Only log this message if the time bucket allows it
    return _buckets[key].is_sampled(record, _rate_limit)


class DDFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        skipped = getattr(record, "skipped", 0)
        if skipped:
            skip_str = f" [{skipped} skipped]"
        else:
            skip_str = ""
        product = getattr(record, "product", None)
        # new syntax
        if product:
            more_info = getattr(record, "more_info", "")
            stack_limit = getattr(record, "stack_limit", 0)
            exec_limit = getattr(record, "stack_limit", 1)
            if stack_limit and record.stack_info:
                record.stack_info = self.format_stack(record.stack_info, stack_limit)
            string_buffer = [f"{record.levelname} {product}::{record.msg % record.args}{more_info}{skip_str}"]
            if record.stack_info:
                string_buffer.append(record.stack_info)
            if record.exc_info:
                string_buffer.extend(traceback.format_exception(record.exc_info[1], limit=exec_limit or None))
            return "\n".join(string_buffer)
        # legacy syntax
        return f"{record.levelname} {super().format(record)}{skip_str}"

    def format_stack(self, stack_info, limit) -> str:
        stack = stack_info.split("\n")
        if len(stack) <= limit * 2 + 1:
            return stack_info
        stack_str = "\n".join(stack[-2 * limit :])
        return f"{stack[0]}\n{stack_str}"


# setup the default formatter for all ddtrace loggers
root_logger = logging.getLogger("ddtrace")
root_logger.handlers.append(logging.StreamHandler())
root_logger.handlers[0].setFormatter(DDFormatter())
root_logger.propagate = True
