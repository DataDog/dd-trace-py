"""Opt-in lock profiling via sys.monitoring CALL/C_RETURN (Python 3.12+).

Research spike behind ``DD_PROFILING_LOCK_USE_SYS_MONITORING``. Avoids
``_ProfiledLock`` / allocator patching for ``threading.Lock`` and
``threading.RLock`` while preserving native lock identity.
"""

from __future__ import annotations

import _thread
import os
import sys
import threading
import time
from types import CodeType
from types import FrameType
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.profiling import config
from ddtrace.profiling import collector
from ddtrace.profiling._threading import get_thread_name
from ddtrace.profiling._threading import get_thread_native_id
from ddtrace.profiling.collector._task import get_task
from ddtrace.trace import Tracer


if sys.version_info < (3, 12):
    raise ImportError("ddtrace.profiling.collector.lock_monitoring requires Python 3.12+")

log = get_logger(__name__)  # type: ignore[unreachable]

_ACQUIRE_NAMES = frozenset({"acquire", "__enter__", "__aenter__"})
_RELEASE_NAMES = frozenset({"release", "__exit__", "__aexit__"})
_THREAD_LOCK_TYPES = (type(threading.Lock()), type(threading.RLock()))

# Prefer slots away from the internal multiplexer (4, 3) and native profiler (2).
_CANDIDATE_TOOL_IDS = (5, 3)

_TOOL_NAME = "ddtrace-lock-profiler"


class LockMonitoringService:
    """Singleton sys.monitoring backend for C lock acquire/release."""

    _instance: Optional[LockMonitoringService] = None

    def __init__(self) -> None:
        self._refcount = 0
        self._tool_id: Optional[int] = None
        self._tracer: Optional[Tracer] = None
        self._capture_sampler: Optional[collector.CaptureSampler] = None
        # (thread_id, id(lock)) -> monotonic_ns when acquire CALL was sampled
        self._pending_acquire_calls: dict[tuple[int, int], int] = {}
        # (thread_id, id(lock)) -> monotonic_ns when acquire completed (hold start)
        self._held_since: dict[tuple[int, int], int] = {}

    @classmethod
    def acquire(cls, lock_collector: Any) -> None:
        """Register a LockCollector that opts into sys.monitoring."""
        if cls._instance is None:
            cls._instance = LockMonitoringService()
        svc = cls._instance
        if svc._refcount == 0:
            svc._tracer = lock_collector.tracer
            svc._capture_sampler = lock_collector._capture_sampler
            svc._start()
        svc._refcount += 1

    @classmethod
    def release(cls, lock_collector: Any) -> None:
        if cls._instance is None:
            return
        svc = cls._instance
        svc._refcount = max(0, svc._refcount - 1)
        if svc._refcount == 0:
            svc._stop()
            cls._instance = None

    def _start(self) -> None:
        for tid in _CANDIDATE_TOOL_IDS:
            existing = sys.monitoring.get_tool(tid)
            if existing is not None and existing != _TOOL_NAME:
                continue
            try:
                if existing is None:
                    sys.monitoring.use_tool_id(tid, _TOOL_NAME)
                self._tool_id = tid
                break
            except ValueError:
                continue
        else:
            raise RuntimeError("No free sys.monitoring tool ID for lock profiling spike")

        events = sys.monitoring.events
        sys.monitoring.register_callback(self._tool_id, events.CALL, self._on_call)
        sys.monitoring.register_callback(self._tool_id, events.C_RETURN, self._on_c_return)
        sys.monitoring.register_callback(self._tool_id, events.C_RAISE, self._on_c_raise)
        sys.monitoring.set_events(
            self._tool_id,
            events.CALL | events.C_RETURN | events.C_RAISE,
        )

    def _stop(self) -> None:
        if self._tool_id is None:
            return
        sys.monitoring.set_events(self._tool_id, 0)
        self._pending_acquire_calls.clear()
        self._held_since.clear()
        self._tool_id = None

    @staticmethod
    def _lock_from_callable(callable_obj: Any) -> Any | None:
        self_obj = getattr(callable_obj, "__self__", None)
        if self_obj is None:
            return None
        if isinstance(self_obj, _THREAD_LOCK_TYPES):
            return self_obj
        return None

    @staticmethod
    def _init_location(code: CodeType) -> str:
        return "%s:%d" % (os.path.basename(code.co_filename), code.co_firstlineno)

    def _on_call(
        self,
        code: CodeType,
        instruction_offset: int,
        callable_obj: Callable[..., Any],
        arg0: object,
    ) -> object | None:
        lock = self._lock_from_callable(callable_obj)
        if lock is None:
            return None

        name = getattr(callable_obj, "__name__", "")
        if name in _ACQUIRE_NAMES:
            sampler = self._capture_sampler
            if sampler is None or not sampler.capture():
                return None
            self._pending_acquire_calls[(_thread.get_ident(), id(lock))] = time.monotonic_ns()
        elif name not in _RELEASE_NAMES:
            return None
        return None

    def _on_c_return(
        self,
        code: CodeType,
        instruction_offset: int,
        callable_obj: Callable[..., Any],
        arg0: object,
    ) -> object | None:
        lock = self._lock_from_callable(callable_obj)
        if lock is None:
            return None

        name = getattr(callable_obj, "__name__", "")
        key = (_thread.get_ident(), id(lock))
        if name in _ACQUIRE_NAMES:
            start = self._pending_acquire_calls.pop(key, None)
            if start is None:
                return None
            end = time.monotonic_ns()
            self._flush_sample(start, end, lock, code, is_acquire=True)
            self._held_since[key] = end
        elif name in _RELEASE_NAMES:
            start = self._held_since.pop(key, None)
            if start is not None:
                self._flush_sample(start, time.monotonic_ns(), lock, code, is_acquire=False)
        return None

    def _on_c_raise(
        self,
        code: CodeType,
        instruction_offset: int,
        callable_obj: Callable[..., Any],
        arg0: object,
    ) -> object | None:
        lock = self._lock_from_callable(callable_obj)
        if lock is None:
            return None
        name = getattr(callable_obj, "__name__", "")
        key = (_thread.get_ident(), id(lock))
        if name in _ACQUIRE_NAMES:
            self._pending_acquire_calls.pop(key, None)
        return None

    def _flush_sample(
        self,
        start: int,
        end: int,
        lock: Any,
        code: CodeType,
        *,
        is_acquire: bool,
    ) -> None:
        try:
            duration_ns = end - start
            handle = ddup.SampleHandle()
            handle.push_monotonic_ns(end)
            handle.push_lock_name(self._init_location(code))
            if is_acquire:
                handle.push_acquire(duration_ns, 1)
            else:
                handle.push_release(duration_ns, 1)

            thread_id = _thread.get_ident()
            thread_name = get_thread_name(thread_id)
            task_id, task_name, task_frame = get_task()
            handle.push_task_id(task_id)
            handle.push_task_name(task_name)
            handle.push_threadinfo(thread_id, get_thread_native_id(thread_id), thread_name)

            if self._tracer is not None:
                handle.push_span(self._tracer.current_span())

            frame: FrameType | None = task_frame
            if frame is None:
                try:
                    frame = sys._getframe(1)
                except ValueError:
                    frame = None
            if frame is not None:
                handle.push_pyframes(frame)

            handle.flush_sample()
        except Exception:
            if config.enable_asserts:
                raise
            log.debug("lock monitoring sample flush failed", exc_info=True)
