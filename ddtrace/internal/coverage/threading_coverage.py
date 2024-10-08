"""Provides functionality for context-based coverage to work across threads

Without this, context-based collection in the parent process would not capture code executed by threads (due to the
parent process' context variables not being shared in threads).

The collection of coverage is done when the thread's join() method is called, so context-level coverage will not be
captured if join() is not called.

Since the ModuleCodeCollector is already installed at the process level, there is no need to reinstall it or ensure that
its include_paths are set.

Session-level coverage does not need special-casing since the ModuleCodeCollector behavior is process-wide and
thread-safe.
"""
import pickle  # nosec: B403  -- pickle is only used to serialize coverage data from a spawned thread to the main thread
from queue import Queue
import threading

from ddtrace.internal.coverage.code import ModuleCodeCollector
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

Thread = threading.Thread
thread_init = Thread.__init__
thread_boostrap_inner = Thread._bootstrap_inner  # type: ignore[attr-defined]
thread_join = Thread.join

DD_PATCH_ATTR = "_datadog_patch"


def _is_patched():
    return hasattr(threading, DD_PATCH_ATTR)


class CoverageCollectingThread(threading.Thread):
    def __init__(self, *args, **kwargs):
        """Wraps the thread initialization creation to enable coverage collection

        Only enables coverage if the parent process' context-level coverage is enabled.
        """
        self._should_cover = ModuleCodeCollector.is_installed() and ModuleCodeCollector.coverage_enabled_in_context()

        if self._should_cover:
            self._coverage_queue = Queue()

        thread_init(self, *args, **kwargs)

    def _bootstrap_inner(self):
        """Collect thread-level coverage data in a context and queue it up for the parent process to absorb"""
        if self._should_cover:
            self._coverage_context = ModuleCodeCollector.CollectInContext()
            self._coverage_context.__enter__()

        try:
            thread_boostrap_inner(self)
        finally:
            # Ensure coverage data is collected, and context is exited, even if an exception is raised
            if self._should_cover:
                try:
                    covered_lines = pickle.dumps({"covered": self._coverage_context.get_covered_lines()})
                except pickle.PicklingError:
                    log.warning("Could not pickle coverage data, not injecting coverage")
                    return
                self._coverage_context.__exit__()
                self._coverage_queue.put(covered_lines)

    def join(self, *args, **kwargs):
        """Absorb coverage data from the thread after it's joined"""
        thread_join(self, *args, **kwargs)
        if self._should_cover:
            if self._coverage_queue.qsize():
                try:
                    data = pickle.loads(self._coverage_queue.get())  # nosec: B301 -- we trust this is coverage data
                    thread_covered = data.get("covered", {})
                except pickle.UnpicklingError:
                    log.warning("Could not unpickle coverage data, not injecting coverage")
                    return
                ModuleCodeCollector.inject_coverage(covered=thread_covered)


def _patch_threading():
    threading.Thread.__init__ = CoverageCollectingThread.__init__
    threading.Thread._bootstrap_inner = CoverageCollectingThread._bootstrap_inner
    threading.Thread.join = CoverageCollectingThread.join
