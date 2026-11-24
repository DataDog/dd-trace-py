"""Handles the records that ddtrace threads log under gevent on the main hub.

Module cloning shares logging with the application, so once gevent has patched threading, the logging lock and the
handler locks are gevent locks. A ddtrace thread that waits for one depends on the main thread's hub, which might
itself be waiting for that thread, e.g. to join it before a fork. Records logged off the main thread are queued
instead, and a single greenlet on the main hub handles them in order.

The ddtrace loggers are patched per instance, wherever they are created, so that nothing changes the class of a logger
and loggers of the application are left alone.
"""

from _queue import Empty
from _queue import SimpleQueue
import copy
import logging
import os
import sys
import traceback
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace.internal import forksafe
from ddtrace.internal import threads
from ddtrace.internal.locks import Lock
from ddtrace.internal.module import ModuleWatchdog


# The application's copy of _thread is patched by gevent, whose get_ident is greenlet-based.
get_ident = threads._get_ident


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
        self._records: SimpleQueue[tuple[logging.Logger, logging.LogRecord]] = SimpleQueue()
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

    def runs_inline(self) -> bool:
        # Until the main hub exists nothing can wait for it, and without patched threading the locks are native.
        return self._hub is None or get_ident() == self._main_thread_ident or not self._is_threading_patched()

    def _is_threading_patched(self) -> bool:
        if not self._threading_patched:
            monkey = sys.modules.get("gevent.monkey")
            self._threading_patched = monkey is not None and monkey.is_module_patched("threading")
        return self._threading_patched

    def put(self, logger: logging.Logger, record: logging.LogRecord) -> bool:
        """Queue the record for the main hub. Returns False when the caller should handle it inline."""
        if os.getpid() != self._pid:
            return False  # In a fork child before reset: the lock and the hub belong to the parent.
        # Keep the arguments as logged, since the caller might change them before the record is handled.
        record.args = copy.copy(record.args)
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
                type(logger).handle(logger, record)
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


main_hub_logging = MainHubLogging()


def _reset_after_fork() -> None:
    main_hub_logging.reset()
    # The thread that forked is the main thread of the child. gevent reinitializes the loop of its hub after the fork
    # hooks, so records go to that hub again once it runs.
    gevent_hub = sys.modules.get("gevent.hub")
    if gevent_hub is not None and (hub := gevent_hub._get_hub()) is not None:
        hub.loop.run_callback(main_hub_logging.set_hub, hub, gevent_hub.spawn_raw)


def _is_ddtrace(name: str) -> bool:
    return name == "ddtrace" or name.startswith("ddtrace.")


def _patch_logger(logger: logging.Logger) -> None:
    """Hand the records of a ddtrace logger to the main hub when they are logged off the main thread."""
    if "handle" in vars(logger):
        return
    cls = type(logger)

    def handle(record: logging.LogRecord) -> None:
        if main_hub_logging.runs_inline() or not main_hub_logging.put(logger, record):
            cls.handle(logger, record)

    def is_enabled_for(level: int) -> bool:
        if logger.disabled:
            return False
        try:
            return bool(logger._cache[level])  # type: ignore[attr-defined]
        except KeyError:
            pass
        if main_hub_logging.runs_inline():
            return cls.isEnabledFor(logger, level)
        # Same as the base class, without the logging lock it takes to fill the cache.
        return level > logger.manager.disable and level >= logger.getEffectiveLevel()

    logger.handle = handle  # type: ignore[method-assign]
    logger.isEnabledFor = is_enabled_for  # type: ignore[method-assign]


def _unpatch_logger(logger: logging.Logger) -> None:
    for name in ("handle", "isEnabledFor"):
        vars(logger).pop(name, None)


def _loggers() -> list[logging.Logger]:
    return [
        logger
        for logger in list(logging.Logger.manager.loggerDict.values())
        if isinstance(logger, logging.Logger) and _is_ddtrace(logger.name)
    ]


_mp_hook_registered = False
_get_logger: Optional[Callable[..., logging.Logger]] = None
_hub_init: Optional[Callable[..., None]] = None
_gevent_hub: Any = None


def _flush_at_exit() -> None:
    main_hub_logging.flush_at_exit()


threads.register_after_threads_stopped(_flush_at_exit)


def install(gevent_hub: Any) -> None:
    """Hand the records of ddtrace threads to the main hub once gevent creates it. Called when gevent is patched."""
    global _get_logger, _hub_init, _gevent_hub, _mp_hook_registered

    if _get_logger is not None:
        return

    forksafe.register(_reset_after_fork)

    if not _mp_hook_registered:
        _mp_hook_registered = True

        @ModuleWatchdog.after_module_imported("multiprocessing.util")
        def _(mp_util: Any) -> None:
            # A multiprocessing child exits with os._exit once these finalizers have run, and skips atexit.
            mp_util.register_after_fork(
                main_hub_logging, lambda m: mp_util.Finalize(m, m.flush_at_exit, exitpriority=0)
            )

    # The single entry point that creates loggers, which also covers modules that call logging.getLogger directly.
    _get_logger = get_logger = logging.Manager.getLogger

    def getLogger(self: logging.Manager, name: str) -> logging.Logger:
        logger = get_logger(self, name)
        if _is_ddtrace(name):
            _patch_logger(logger)
        return logger

    logging.Manager.getLogger = getLogger  # type: ignore[method-assign]
    for logger in _loggers():
        _patch_logger(logger)

    _gevent_hub = gevent_hub
    _hub_init = hub_init = gevent_hub.Hub.__init__

    def __init__(self: Any, *args: Any, **kwargs: Any) -> None:
        hub_init(self, *args, **kwargs)
        main_hub_logging.set_hub(self, gevent_hub.spawn_raw)

    gevent_hub.Hub.__init__ = __init__
    # gevent might have been imported, and its main hub created, before ddtrace patched it.
    if (hub := gevent_hub._get_hub()) is not None:
        main_hub_logging.set_hub(hub, gevent_hub.spawn_raw)


def uninstall() -> None:
    """Handle the loggers inline again, and emit the records queued. Called when gevent is unpatched."""
    global _get_logger, _hub_init, _gevent_hub

    if _get_logger is None:
        return

    logging.Manager.getLogger = _get_logger  # type: ignore[method-assign]
    _gevent_hub.Hub.__init__ = _hub_init
    _get_logger = _hub_init = _gevent_hub = None
    forksafe.unregister(_reset_after_fork)
    for logger in _loggers():
        _unpatch_logger(logger)
    main_hub_logging.flush_at_exit()
