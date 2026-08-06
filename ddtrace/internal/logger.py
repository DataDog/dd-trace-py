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

from _thread import allocate_lock as _allocate_native_lock
from _thread import get_ident as _get_native_ident
import collections
from dataclasses import dataclass
from dataclasses import field
import functools
import logging
import time
import traceback
from typing import Callable
from typing import DefaultDict
from typing import Deque
from typing import Optional
from typing import Protocol
from typing import Tuple
from typing import Union

from ddtrace.internal import atexit
from ddtrace.internal.settings import env


SECOND = 1
MINUTE = 60 * SECOND
HOUR = 60 * MINUTE
DAY = 24 * HOUR


class _GeventLoop(Protocol):
    def run_callback_threadsafe(self, callback: Callable[..., object], *args: object) -> object: ...


class _GeventHub(Protocol):
    loop: _GeventLoop


_DEFERRED_LOG_RECORD_LIMIT = 1024
_DEFERRED_LOG_RECORDS: Deque[logging.LogRecord] = collections.deque(maxlen=_DEFERRED_LOG_RECORD_LIMIT)  # noqa: UP006
_DEFERRED_LOG_STATE_LOCK = _allocate_native_lock()
_DEFERRED_RECORD_ATTRIBUTE = "_dd_deferred"
_DEFERRED_RECORD_SENTINEL = object()
_GEVENT_THREADING_PATCHED = False
_GEVENT_FORKSAFE_REGISTERED = False
_GEVENT_DRAIN_SCHEDULED = False
_GEVENT_HUB: Optional[_GeventHub] = None
_GEVENT_SPAWN_RAW: Optional[Callable[..., object]] = None
_MAIN_THREAD_IDENT = _get_native_ident()


def _drain_deferred_log_records() -> None:
    """Emit one bounded batch of deferred records from a gevent-managed greenlet."""
    global _GEVENT_DRAIN_SCHEDULED

    try:
        # Bound each drain to the records present on entry so producers cannot starve the hub thread.
        for _ in range(len(_DEFERRED_LOG_RECORDS)):
            try:
                record = _DEFERRED_LOG_RECORDS.popleft()
            except IndexError:
                break
            setattr(record, _DEFERRED_RECORD_ATTRIBUTE, _DEFERRED_RECORD_SENTINEL)
            logging.getLogger(record.name).handle(record)
    finally:
        with _DEFERRED_LOG_STATE_LOCK:
            _GEVENT_DRAIN_SCHEDULED = False
            schedule_another_drain = bool(_DEFERRED_LOG_RECORDS)
        if schedule_another_drain:
            _schedule_deferred_log_drain()


atexit.register(_drain_deferred_log_records)


def _schedule_deferred_log_drain() -> None:
    """Wake the owning gevent hub when a native thread defers a log record."""
    global _GEVENT_DRAIN_SCHEDULED

    with _DEFERRED_LOG_STATE_LOCK:
        hub = _GEVENT_HUB
        spawn_raw = _GEVENT_SPAWN_RAW
        if _GEVENT_DRAIN_SCHEDULED or hub is None or spawn_raw is None:
            return
        _GEVENT_DRAIN_SCHEDULED = True

    try:
        # Loop callbacks cannot yield, but logging handlers can. Start a raw greenlet before invoking them.
        hub.loop.run_callback_threadsafe(spawn_raw, _drain_deferred_log_records)
    except Exception:
        # The hub can already be destroyed during interpreter shutdown. The atexit drain remains as a fallback.
        with _DEFERRED_LOG_STATE_LOCK:
            _GEVENT_DRAIN_SCHEDULED = False


def _after_fork() -> None:
    global _DEFERRED_LOG_STATE_LOCK, _GEVENT_DRAIN_SCHEDULED, _GEVENT_HUB, _MAIN_THREAD_IDENT

    from gevent.hub import get_hub

    _DEFERRED_LOG_RECORDS.clear()
    _DEFERRED_LOG_STATE_LOCK = _allocate_native_lock()
    _GEVENT_DRAIN_SCHEDULED = False
    _GEVENT_HUB = get_hub()
    _MAIN_THREAD_IDENT = _get_native_ident()


def _enable_gevent_thread_logging(gevent_monkey) -> None:
    global _DEFERRED_LOG_STATE_LOCK, _GEVENT_FORKSAFE_REGISTERED, _GEVENT_HUB, _GEVENT_SPAWN_RAW
    global _GEVENT_THREADING_PATCHED, _MAIN_THREAD_IDENT, _allocate_native_lock, _get_native_ident

    if _GEVENT_THREADING_PATCHED or not gevent_monkey.is_module_patched("threading"):
        return

    # ddtrace can be imported after monkey.patch_all(), in which case the module-level alias was patched too.
    _allocate_native_lock = gevent_monkey.get_original("_thread", "allocate_lock")
    _get_native_ident = gevent_monkey.get_original("_thread", "get_ident")
    from gevent.hub import get_hub
    from gevent.hub import spawn_raw

    # Capture the patching thread's hub. Calling get_hub() later from a foreign worker would create an unused hub.
    _GEVENT_HUB = get_hub()
    _GEVENT_SPAWN_RAW = spawn_raw
    _MAIN_THREAD_IDENT = _get_native_ident()
    _DEFERRED_LOG_STATE_LOCK = _allocate_native_lock()
    _GEVENT_THREADING_PATCHED = True
    if _GEVENT_FORKSAFE_REGISTERED:
        return

    from ddtrace.internal import forksafe

    forksafe.register(_after_fork)
    _GEVENT_FORKSAFE_REGISTERED = True


def configure_gevent_logging(gevent_monkey) -> None:
    """Route native-thread records through the gevent hub after threading is patched."""
    if getattr(gevent_monkey, "_ddtrace_logging_wrapped", False):
        _enable_gevent_thread_logging(gevent_monkey)
        return

    original_patch_all = gevent_monkey.patch_all
    original_patch_thread = gevent_monkey.patch_thread

    @functools.wraps(original_patch_all)
    def patch_all(*args, **kwargs):
        result = original_patch_all(*args, **kwargs)
        _enable_gevent_thread_logging(gevent_monkey)
        return result

    @functools.wraps(original_patch_thread)
    def patch_thread(*args, **kwargs):
        result = original_patch_thread(*args, **kwargs)
        _enable_gevent_thread_logging(gevent_monkey)
        return result

    gevent_monkey.patch_all = patch_all
    gevent_monkey.patch_thread = patch_thread
    gevent_monkey._ddtrace_logging_wrapped = True
    _enable_gevent_thread_logging(gevent_monkey)


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
    gevent_threading_patched = _GEVENT_THREADING_PATCHED
    is_main_thread = False
    if gevent_threading_patched:
        is_main_thread = _get_native_ident() == _MAIN_THREAD_IDENT
        if is_main_thread and getattr(record, _DEFERRED_RECORD_ATTRIBUTE, None) is _DEFERRED_RECORD_SENTINEL:
            delattr(record, _DEFERRED_RECORD_ATTRIBUTE)
            return True

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
    # AIDEV-NOTE: A gevent Handler lock can permanently strand a greenlet when a hubless native thread releases it.
    # Queue foreign-thread records before Handler.handle and wake the owning hub so it can emit them promptly.
    if must_be_propagated and gevent_threading_patched and not is_main_thread:
        _DEFERRED_LOG_RECORDS.append(record)
        _schedule_deferred_log_drain()
        return False
    return must_be_propagated


def format_stack(stack_info, limit) -> str:
    stack = stack_info.split("\n")
    if len(stack) <= limit * 2 + 1:
        return stack_info
    stack_str = "\n".join(stack[-2 * limit :])
    return f"{stack[0]}\n{stack_str}"


class LogInjectionState(object):
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
