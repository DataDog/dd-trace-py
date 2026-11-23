"""Handles the records that ddtrace threads log under gevent on the main hub.

Module cloning shares logging with the application, so once gevent has patched threading, the logging lock and the
handler locks are gevent locks. A ddtrace thread that waits for one depends on the main thread's hub, which might
itself be waiting for that thread, e.g. to join it before a fork. Records logged off the main thread are queued
instead, and a single greenlet on the main hub handles them in order.
"""

from _queue import Empty
from _queue import SimpleQueue
from _thread import get_ident
import logging
import os
import sys
import traceback
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace.internal import forksafe
from ddtrace.internal.locks import Lock
from ddtrace.internal.logger import _RATE_LIMITS
from ddtrace.internal.logger import HandoffLogger
from ddtrace.internal.logger import set_log_handoff
from ddtrace.internal.module import ModuleWatchdog


class MainHubLogging:
    """Runs the logging handlers of ddtrace loggers on the main thread once gevent has a hub there."""

    # A main hub that doesn't run, e.g. busy with CPU work, makes threads log inline past this many records.
    max_records = 1000

    def __init__(self) -> None:
        self._threading_patched = False
        self.reset()

    def reset(self) -> None:
        """Log inline in a fork child until a main hub runs there: the parent emits the records it had queued, and
        the child might exit with os._exit before. The thread that forked is the main thread of the child.
        """
        self._pid = os.getpid()
        self._main_thread_ident = get_ident()
        self._lock = Lock()
        self._records: SimpleQueue[tuple[HandoffLogger, logging.LogRecord]] = SimpleQueue()
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
        set_log_handoff(self)

    def runs_inline(self) -> bool:
        # Until the main hub exists nothing can wait for it, and without patched threading the locks are native.
        return self._hub is None or get_ident() == self._main_thread_ident or not self._is_threading_patched()

    def _is_threading_patched(self) -> bool:
        if not self._threading_patched:
            monkey = sys.modules.get("gevent.monkey")
            self._threading_patched = monkey is not None and monkey.is_module_patched("threading")
        return self._threading_patched

    def put(self, logger: HandoffLogger, record: logging.LogRecord) -> bool:
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
                logger.handle_inline(record)
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
    except Exception:  # nosec B110
        pass  # The handler reports the error


main_hub_logging = MainHubLogging()


def _reset_after_fork() -> None:
    main_hub_logging.reset()
    # The thread that forked is the main thread of the child. gevent reinitializes the loop of its hub after the fork
    # hooks, so records go to that hub again once it runs.
    gevent_hub = sys.modules.get("gevent.hub")
    if gevent_hub is not None and (hub := gevent_hub._get_hub()) is not None:
        hub.loop.run_callback(main_hub_logging.set_hub, hub, gevent_hub.spawn_raw)


_installed = False


def install(gevent_hub: Any) -> None:
    """Hand the records of ddtrace threads to the main hub once gevent creates it. Called when gevent is patched.

    Kept on unpatch, as unpatching gevent doesn't make the logging locks native again.
    """
    global _installed

    if _installed:
        return
    _installed = True

    forksafe.register(_reset_after_fork)

    @ModuleWatchdog.after_module_imported("multiprocessing.util")
    def _(mp_util: Any) -> None:
        # A multiprocessing child exits with os._exit once these finalizers have run, and skips atexit.
        mp_util.register_after_fork(main_hub_logging, lambda m: mp_util.Finalize(m, m.flush_at_exit, exitpriority=0))

    hub_init = gevent_hub.Hub.__init__

    def __init__(self: Any, *args: Any, **kwargs: Any) -> None:
        hub_init(self, *args, **kwargs)
        main_hub_logging.set_hub(self, gevent_hub.spawn_raw)

    gevent_hub.Hub.__init__ = __init__
    # gevent might have been imported, and its main hub created, before ddtrace patched it.
    if (hub := gevent_hub._get_hub()) is not None:
        main_hub_logging.set_hub(hub, gevent_hub.spawn_raw)
