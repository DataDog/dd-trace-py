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

from _queue import Empty
from _queue import SimpleQueue
from _thread import get_ident
import atexit
import collections
from dataclasses import dataclass
from dataclasses import field
from functools import cache
import logging
import os
import sys
import time
import traceback
from typing import Any
from typing import Callable
from typing import DefaultDict
from typing import Optional
from typing import Tuple
from typing import Union

from ddtrace.internal.locks import Lock
from ddtrace.internal.settings import env


SECOND = 1
MINUTE = 60 * SECOND
HOUR = 60 * MINUTE
DAY = 24 * HOUR


@dataclass
class LoggerPrefix:
    prefix: str
    level: Optional[int] = None
    children: dict[str, "LoggerPrefix"] = field(default_factory=dict)

    def lookup(self, name: str) -> Optional[int]:
        """
        Lookup the log level for a given logger name in the trie.

        The name is split by '.' and each part is used to traverse the trie.
        If a part is not found, it returns the level of the closest parent node.
        """
        parts = name.replace("_", ".").lower().split(".")
        parts.pop(0)  # remove the ddtrace prefix
        current = self
        while parts:
            if (part := parts.pop(0)) not in current.children:
                return current.level
            current = current.children[part]

        return current.level

    @classmethod
    def build_trie(cls):
        trie = cls(prefix="ddtrace", level=None, children={})

        for logger_name, level in ((k, v) for k, v in env.items() if k.startswith("_DD_") and k.endswith("_LOG_LEVEL")):
            # Remove the _DD_ prefix and _LOG_LEVEL suffix
            logger_name = logger_name[4:-10]
            parts = logger_name.lower().split("_")
            current = trie.children
            while parts:
                if (part := parts.pop(0)) not in current:
                    current[part] = cls(prefix=part, level=getattr(logging, level, None) if not parts else None)
                current = current[part].children

        return trie


LOG_LEVEL_TRIE = LoggerPrefix.build_trie()


def get_logger(name: str) -> logging.Logger:
    """
    Retrieve or create a ``Logger`` instance with consistent behavior for internal use.

    Configure all loggers with a rate limiter filter to prevent excessive logging.

    """
    logger = logging.getLogger(name)
    # addFilter will only add the filter if it is not already present
    logger.addFilter(log_filter)

    # Set the log level from the environment variable of the closest parent
    # logger.
    if name.startswith("ddtrace."):  # for the whole of ddtrace we have DD_TRACE_DEBUG
        if (level := LOG_LEVEL_TRIE.lookup(name)) is not None:
            logger.setLevel(level)

    return logger


class _MainHubLogger(logging.Logger):
    """A ddtrace logger that hands the records produced off the main thread to the main gevent hub."""

    def isEnabledFor(self, level: int) -> bool:
        if self.disabled:
            return False
        try:
            return self._cache[level]  # type: ignore[attr-defined]
        except KeyError:
            pass
        if main_hub_logging.runs_inline():
            return super().isEnabledFor(level)
        # Same as the base class, without the logging lock it takes to fill the cache.
        return level > self.manager.disable and level >= self.getEffectiveLevel()

    def handle(self, record: logging.LogRecord) -> None:
        if main_hub_logging.runs_inline() or not main_hub_logging.put(self, record):
            super().handle(record)


@cache
def _main_hub_logger_class(cls: type) -> type:
    return _MainHubLogger if cls is logging.Logger else type(cls.__name__, (_MainHubLogger, cls), {})


class _MainHubManager(logging.Manager):
    def getLogger(self, name: str) -> logging.Logger:
        logger = super().getLogger(name)
        _wrap(logger)
        return logger


def _wrap(logger: logging.Logger) -> None:
    if not isinstance(logger, _MainHubLogger) and (logger.name == "ddtrace" or logger.name.startswith("ddtrace.")):
        try:
            logger.__class__ = _main_hub_logger_class(type(logger))  # type: ignore[arg-type]
        except TypeError:
            pass  # e.g. a logger class with __slots__, which keeps logging inline


class MainHubLogging:
    """Runs the logging handlers of ddtrace loggers on the main thread once gevent has a hub there.

    Module cloning shares logging with the application, so once gevent has patched threading, the logging lock and
    the handler locks are gevent locks. A ddtrace thread that waits for one depends on the main thread's hub, which
    might itself be waiting for that thread, e.g. to join it before a fork. Records logged off the main thread are
    queued instead, and a single greenlet on the main hub handles them in order.
    """

    # A main hub that doesn't run, e.g. busy with CPU work, makes threads log inline past this many records.
    max_records = 1000

    def __init__(self) -> None:
        self._threading_patched = False
        self.reset()

    def reset(self) -> None:
        """Log inline in a fork child: the parent emits the records it had queued, and the child might never run a
        hub, or exit with os._exit. The thread that forked is the main thread of the child.
        """
        self._pid = os.getpid()
        self._main_thread_ident = get_ident()
        self._lock = Lock()
        self._records: SimpleQueue[tuple[_MainHubLogger, logging.LogRecord]] = SimpleQueue()
        self._draining = False
        self._hub: Any = None
        self._spawn_raw: Optional[Callable[..., Any]] = None

    def set_hub(self, hub: Any, spawn_raw: Callable[..., Any]) -> None:
        """Hand records to the hub created on the main thread, which owns the gevent locks of logging."""
        if get_ident() != self._main_thread_ident:
            return
        with self._lock:
            # A thread that sees the hub uses spawn_raw.
            self._spawn_raw = spawn_raw
            self._hub = hub
        manager = logging.Logger.manager
        if type(manager) is logging.Manager:
            manager.__class__ = _MainHubManager
        for logger in list(manager.loggerDict.values()):
            if isinstance(logger, logging.Logger):
                _wrap(logger)

    def runs_inline(self) -> bool:
        # Until the main hub exists nothing can wait for it, and without patched threading the locks are native.
        return self._hub is None or get_ident() == self._main_thread_ident or not self._is_threading_patched()

    def _is_threading_patched(self) -> bool:
        if not self._threading_patched:
            monkey = sys.modules.get("gevent.monkey")
            self._threading_patched = monkey is not None and monkey.is_module_patched("threading")
        return self._threading_patched

    def put(self, logger: _MainHubLogger, record: logging.LogRecord) -> bool:
        """Queue the record for the main hub. Returns False when the caller should handle it inline."""
        if os.getpid() != self._pid:
            return False  # In a fork child before reset: the lock and the hub belong to the parent.
        _format_args(record)
        with self._lock:
            hub, spawn_raw = self._hub, self._spawn_raw
            if hub is None or self._records.qsize() >= self.max_records:
                return False
            self._records.put((logger, record))
            if self._draining:
                return True
            self._draining = True
        try:
            # Spawning needs the hub's own thread. This only wakes up its loop, and doesn't block.
            hub.loop.run_callback_threadsafe(spawn_raw, self._drain)
        except Exception:
            # The hub is gone; the records are emitted at exit.
            with self._lock:
                self._draining = False
        return True

    def flush(self) -> None:
        """Handle the queued records on the current thread."""
        while True:
            try:
                logger, record = self._records.get_nowait()
            except Empty:
                return
            try:
                super(_MainHubLogger, logger).handle(record)
            except Exception:
                if logging.raiseExceptions:
                    traceback.print_exc()

    def flush_at_exit(self) -> None:
        # Runs after the native threads have joined, so it emits what they logged while stopping.
        with self._lock:
            # Nothing will run the hub anymore, so log inline from now on.
            self._hub = None
        self.flush()

    def _drain(self) -> None:
        try:
            while True:
                self.flush()
                with self._lock:
                    if self._records.empty():
                        self._draining = False
                        return
        except BaseException:
            with self._lock:
                self._draining = False
            raise


def _format_args(record: logging.LogRecord) -> None:
    """Format the record while its arguments still hold the values they were logged with."""
    # The rate limiter keys product records on their message, and formats them itself.
    if hasattr(record, "product") or record.msg in _RATE_LIMITS:
        return
    try:
        if record.args:
            record.msg, record.args = record.getMessage(), None
        if record.exc_info:
            record.exc_text, record.exc_info = logging.Formatter().formatException(record.exc_info), None
    except Exception:
        pass  # The handler reports the error


main_hub_logging = MainHubLogging()
# Registered before the native threads module registers the hook that joins them, so that it runs after it.
atexit.register(main_hub_logging.flush_at_exit)


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


# dict to keep track of the current time bucket per name/level/pathname/lineno

_MINF = float("-inf")

# IMPORTANT: Do not change typing types to built-ins until minimum Python version is 3.11+
# Module-level tuple[...] and defaultdict[...] in Python 3.10 affect import timing. See packages.py for details.
key_type = Union[Tuple[str, int, str, int], str]  # noqa: UP006
_buckets: DefaultDict[key_type, LoggingBucket] = collections.defaultdict(lambda: LoggingBucket(_MINF, 0))  # noqa: UP006

# Allow 1 log record per name/level/pathname/lineno every 60 seconds by default
# Allow configuring via `DD_TRACE_LOGGING_RATE`
# DEV: `DD_TRACE_LOGGING_RATE=0` means to disable all rate limiting
_rate_limit = int(env.get("DD_TRACE_LOGGING_RATE", default=60))


def log_filter(record: logging.LogRecord) -> bool:
    """
    Function used to determine if a log record should be outputted or not (True = output, False = skip).

    This function will:
      - Rate limit log records based on the logger name, record level, filename, and line number
    """
    logger = logging.getLogger(record.name)
    rate_limit = _RATE_LIMITS.get(record.msg, _rate_limit)
    # If rate limiting has been disabled (`DD_TRACE_LOGGING_RATE=0`) then apply no rate limit
    # If the logger is set to debug, then do not apply any limits to any log
    if not rate_limit or logger.getEffectiveLevel() == logging.DEBUG:
        must_be_propagated = True
    else:
        # Allow 1 log record by pathname/lineno every X seconds or message/levelno for product logs
        # This way each unique log message can get logged at least once per time period
        if hasattr(record, "product"):
            key: key_type = record.msg
        else:
            key = (record.name, record.levelno, record.pathname, record.lineno)
        # If rate limiting has been disabled (`DD_TRACE_LOGGING_RATE=0`) then apply no rate limit
        # If the logger is set to debug, then do not apply any limits to any log
        # Only log this message if the time bucket allows it
        must_be_propagated = _buckets[key].is_sampled(record, rate_limit)
    if must_be_propagated:
        skipped = record.__dict__.pop("skipped", 0)
        if skipped:
            skip_str = f" [{skipped} skipped]"
        else:
            skip_str = ""
        product = record.__dict__.pop("product", None)
        # new syntax
        if product:
            more_info = record.__dict__.pop("more_info", "")
            stack_limit = record.__dict__.pop("stack_limit", 0)
            exec_limit = record.__dict__.pop("exec_limit", 1)
            # format the stacks if they are present with the right depth
            if stack_limit and record.stack_info:
                record.stack_info = format_stack(record.stack_info, stack_limit)
            string_buffer = [f"{product}::{record.msg}{more_info}{skip_str}"]
            if record.stack_info:
                string_buffer.append(record.stack_info)
            if record.exc_info:
                string_buffer.extend(traceback.format_exception(record.exc_info[1], limit=exec_limit or None))
            record.msg = "\n".join(string_buffer)
            # clean the record for any subsequent handlers
            record.stack_info = None
            record.exc_info = None
        else:
            record.msg = f"{record.msg}{skip_str}"
    return must_be_propagated


def format_stack(stack_info, limit) -> str:
    stack = stack_info.split("\n")
    if len(stack) <= limit * 2 + 1:
        return stack_info
    stack_str = "\n".join(stack[-2 * limit :])
    return f"{stack[0]}\n{stack_str}"


class LogInjectionState:
    # Log injection is disabled
    DISABLED = "false"
    # Log injection is enabled, but not yet configured
    ENABLED = "true"
    # Log injection is enabled and configured for structured logging
    # This value is deprecated, but kept for backwards compatibility
    STRUCTURED = "structured"


def get_log_injection_state(raw_config: Optional[str]) -> bool:
    if raw_config:
        normalized = raw_config.lower().strip()
        if normalized == LogInjectionState.STRUCTURED or normalized in ("true", "1"):
            return True
        elif normalized not in ("false", "0"):
            logging.warning(
                "Invalid log injection state '%s'. Expected 'true', 'false', or 'structured'. Defaulting to 'false'.",
                normalized,
            )
    return False
