import collections
import logging
import os
from typing import DefaultDict
from typing import Tuple


def get_logger(name: str) -> logging.Logger:
    """
    Retrieve or create a ``Logger`` instance with consistent behavior for internal use.

    Configure all loggers with a rate limiter filter to prevent excessive logging.

    """
    logger = logging.getLogger(name)
    # addFilter will only add the filter if it is not already present
    logger.addFilter(log_filter)
    return logger


# Named tuple used for keeping track of a log lines current time bucket and the number of log lines skipped
LoggingBucket = collections.namedtuple("LoggingBucket", ("bucket", "skipped"))
# Dict to keep track of the current time bucket per name/level/pathname/lineno
_buckets: DefaultDict[Tuple[str, int, str, int], LoggingBucket] = collections.defaultdict(lambda: LoggingBucket(0, 0))

# Allow 1 log record per name/level/pathname/lineno every 60 seconds by default
# Allow configuring via `DD_TRACE_LOGGING_RATE`
# DEV: `DD_TRACE_LOGGING_RATE=0` means to disable all rate limiting
_rate_limit = int(os.getenv("DD_TRACE_LOGGING_RATE", default=60))


def log_filter(record: logging.LogRecord) -> bool:
    """
    Function used to determine if a log record should be outputted or not (True = output, False = skip).

    This function will:
      - Log all records with a level of ERROR or higher with telemetry
      - Rate limit log records based on the logger name, record level, filename, and line number
    """
    if record.levelno >= logging.ERROR:
        # avoid circular import
        from ddtrace.internal import telemetry

        # currently we only have one error code
        full_file_name = os.path.join(record.pathname, record.filename)
        telemetry.telemetry_writer.add_error(1, record.msg % record.args, full_file_name, record.lineno)

    logger = logging.getLogger(record.name)

    # If rate limiting has been disabled (`DD_TRACE_LOGGING_RATE=0`) then apply no rate limit
    # If the logger is set to debug, then do not apply any limits to any log
    if not _rate_limit or logger.getEffectiveLevel() == logging.DEBUG:
        return True
        # Allow 1 log record by name/level/pathname/lineno every X seconds
    # DEV: current unix time / rate (e.g. 300 seconds) = time bucket
    #      int(1546615098.8404942 / 300) = 515538
    # DEV: LogRecord `created` is a unix timestamp/float
    # DEV: LogRecord has `levelname` and `levelno`, we want `levelno` e.g. `logging.DEBUG = 10`
    current_bucket = int(record.created / _rate_limit)
    # Limit based on logger name, record level, filename, and line number
    #   ('ddtrace.writer', 'DEBUG', '../site-packages/ddtrace/writer.py', 137)
    # This way each unique log message can get logged at least once per time period
    # DEV: LogRecord has `levelname` and `levelno`, we want `levelno` e.g. `logging.DEBUG = 10`
    key = (record.name, record.levelno, record.pathname, record.lineno)
    # Only log this message if the time bucket has changed from the previous time we ran
    logging_bucket = _buckets[key]
    if logging_bucket.bucket != current_bucket:
        # Append count of skipped messages if we have skipped some since our last logging
        if logging_bucket.skipped:
            record.msg = "{}, %s additional messages skipped".format(record.msg)
            record.args = record.args + (logging_bucket.skipped,)  # type: ignore
            # Reset our bucket
        _buckets[key] = LoggingBucket(current_bucket, 0)
        # Actually log this record
        return True
    # Increment the count of records we have skipped
    # DEV: `buckets[key]` is a tuple which is immutable so recreate instead
    _buckets[key] = LoggingBucket(logging_bucket.bucket, logging_bucket.skipped + 1)
    # Skip this log message
    return False
