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

import contextvars
import pickle  # nosec: B403  -- pickle is only used to serialize coverage data from a spawned thread to the main thread
from queue import Queue
import sys
import threading

from ddtrace.internal.coverage import native_lines
from ddtrace.internal.coverage.code import ModuleCodeCollector
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog


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
        if native_lines.enabled():
            # Native collection credits the thread's lines to the context that started it. A pool
            # worker carries no context of its own: each work item brings the submitter's.
            self._native_parent = None if _is_pool_worker(args, kwargs) else native_lines.ctx_native.get()
            # A context passed to the thread (Python 3.14+) is the caller's, and stays untouched.
            self._native_context_given = kwargs.get("context") is not None
            self._should_cover = False
        else:
            self._native_parent = None
            self._should_cover = (
                ModuleCodeCollector.is_installed() and ModuleCodeCollector.coverage_enabled_in_context()
            )

        if self._should_cover:
            self._coverage_queue = Queue()

        thread_init(self, *args, **kwargs)

    def _bootstrap_inner(self):
        """Collect thread-level coverage data in a context and queue it up for the parent process to absorb"""
        # Threads created before _patch_threading() never ran the patched __init__, so they
        # have no coverage state: collect nothing for them.
        native_parent = getattr(self, "_native_parent", None)
        if native_parent is not None:
            native_lines.ctx_native.set(native_parent)
            _set_in_thread_context(self, native_parent)
        _should_cover = getattr(self, "_should_cover", False)
        if _should_cover:
            self._coverage_context = ModuleCodeCollector.CollectInContext()
            self._coverage_context.__enter__()

        try:
            thread_boostrap_inner(self)
        finally:
            # Ensure coverage data is collected, and context is exited, even if an exception is raised
            if _should_cover:
                covered_lines = None
                try:
                    covered_lines = pickle.dumps({"covered": self._coverage_context.get_covered_lines()})
                except pickle.PicklingError:
                    log.warning("Could not pickle coverage data, not injecting coverage")
                self._coverage_context.__exit__()
                if covered_lines is not None:
                    self._coverage_queue.put(covered_lines)

    def join(self, *args, **kwargs):
        """Absorb coverage data from the thread after it's joined"""
        thread_join(self, *args, **kwargs)
        if getattr(self, "_should_cover", False):
            if self._coverage_queue.qsize():
                try:
                    data = pickle.loads(self._coverage_queue.get())  # nosec: B301 -- we trust this is coverage data
                    thread_covered = data.get("covered", {})
                except pickle.UnpicklingError:
                    log.warning("Could not unpickle coverage data, not injecting coverage")
                    return
                ModuleCodeCollector.inject_coverage(covered=thread_covered)


def _set_in_thread_context(thread, native_parent) -> None:
    # Python 3.14 runs the target in the context that start() made for the thread, an empty one unless
    # the interpreter inherits contexts, so the variable set above is not visible there.
    if sys.version_info >= (3, 14) and not getattr(thread, "_native_context_given", False):
        context = getattr(thread, "_context", None)
        if isinstance(context, contextvars.Context):
            try:
                context.run(native_lines.ctx_native.set, native_parent)
            except Exception:
                log.debug("Could not set the native coverage context in the thread's context", exc_info=True)


def _is_pool_worker(args, kwargs) -> bool:
    # Thread(group, target, ...): ThreadPoolExecutor passes target by keyword.
    target = kwargs.get("target", args[1] if len(args) > 1 else None)
    return getattr(target, "__module__", None) == "concurrent.futures.thread" and (
        getattr(target, "__name__", None) == "_worker"
    )


def _run_in_native_context(native_context, fn, /, *args, **kwargs):
    previous = native_lines.ctx_native.get()
    native_lines.ctx_native.set(native_context)
    try:
        return fn(*args, **kwargs)
    finally:
        native_lines.ctx_native.set(previous)


def _patch_executor_submit(module) -> None:
    executor = module.ThreadPoolExecutor
    submit = executor.submit
    if getattr(submit, DD_PATCH_ATTR, False):
        return

    def submit_in_native_context(self, fn, /, *args, **kwargs):
        native_context = native_lines.ctx_native.get()
        if native_context is None:
            return submit(self, fn, *args, **kwargs)
        return submit(self, _run_in_native_context, native_context, fn, *args, **kwargs)

    setattr(submit_in_native_context, DD_PATCH_ATTR, True)
    executor.submit = submit_in_native_context


def _carry_native_context_into(context) -> None:
    # A task created with an explicit context runs in that context, not in a copy of its creator's, so it
    # would run for no test. The creator's native context is written into it, unless it names one already.
    # Copies made of it later, for the tasks and callbacks it starts, carry it too.
    native_context = native_lines.ctx_native.get()
    if native_context is None or not isinstance(context, contextvars.Context):
        return
    if context.get(native_lines.ctx_native) is None:
        try:
            context.run(native_lines.ctx_native.set, native_context)
        except RuntimeError:
            # The context is entered already, here or on another thread.
            pass


def _patch_create_task(loop_type) -> None:
    create_task = loop_type.create_task
    if getattr(create_task, DD_PATCH_ATTR, False):
        return

    # Arguments pass through as given: from Python 3.14 create_task hands every keyword to the task factory,
    # which need not accept a context=None the caller left out.
    def create_task_in_native_context(self, coro, *args, **kwargs):
        context = kwargs.get("context")
        if context is not None:
            _carry_native_context_into(context)
        return create_task(self, coro, *args, **kwargs)

    setattr(create_task_in_native_context, DD_PATCH_ATTR, True)
    loop_type.create_task = create_task_in_native_context


def _patch_asyncio_create_task(module) -> None:
    # asyncio.create_task() and TaskGroup.create_task() call the loop's create_task.
    _patch_create_task(module.BaseEventLoop)


def _patch_uvloop_create_task(module) -> None:
    loop_type = getattr(module, "Loop", None)
    if loop_type is not None:
        _patch_create_task(loop_type)


def _patch_threading():
    threading.Thread.__init__ = CoverageCollectingThread.__init__
    threading.Thread._bootstrap_inner = CoverageCollectingThread._bootstrap_inner
    threading.Thread.join = CoverageCollectingThread.join
    if native_lines.requested():
        # Work items carry the submitter's native context (per unit of work, not per worker thread).
        ModuleWatchdog.after_module_imported("concurrent.futures.thread")(_patch_executor_submit)
        # So do event loop tasks created with an explicit context.
        ModuleWatchdog.after_module_imported("asyncio.base_events")(_patch_asyncio_create_task)
        ModuleWatchdog.after_module_imported("uvloop")(_patch_uvloop_create_task)
