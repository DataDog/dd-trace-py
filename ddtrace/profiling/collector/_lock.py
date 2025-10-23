from __future__ import absolute_import
from __future__ import annotations

import _thread
import abc
import os.path
import sys
import time
from types import CodeType
from types import FrameType
from types import ModuleType
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Type

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling import _threading
from ddtrace.profiling import collector
from ddtrace.profiling.collector import _task
from ddtrace.profiling.collector import _traceback
from ddtrace.profiling.event import DDFrame
from ddtrace.settings.profiling import config
from ddtrace.trace import Tracer


def _current_thread() -> Tuple[int, str]:
    thread_id: int = _thread.get_ident()
    return thread_id, _threading.get_thread_name(thread_id)


class _ProfiledLock:
    """Lightweight lock wrapper that profiles lock acquire/release operations.
    
    This is a simple delegating wrapper that intercepts lock methods without
    the overhead of a full proxy object.
    """
    
    __slots__ = (
        "__wrapped__",
        "_self_tracer",
        "_self_max_nframes",
        "_self_capture_sampler",
        "_self_endpoint_collection_enabled",
        "_self_init_loc",
        "_self_acquired_at",
        "_self_name",
    )
    
    def __init__(
        self,
        wrapped: Any,
        tracer: Optional[Tracer],
        max_nframes: int,
        capture_sampler: collector.CaptureSampler,
        endpoint_collection_enabled: bool,
    ) -> None:
        self.__wrapped__: Any = wrapped
        self._self_tracer: Optional[Tracer] = tracer
        self._self_max_nframes: int = max_nframes
        self._self_capture_sampler: collector.CaptureSampler = capture_sampler
        self._self_endpoint_collection_enabled: bool = endpoint_collection_enabled
        # Frame depth: 0=__init__, 1=_profiled_allocate_lock, 2=_LockAllocatorWrapper.__call__, 3=caller
        frame: FrameType = sys._getframe(3)
        code: CodeType = frame.f_code
        self._self_init_loc: str = "%s:%d" % (os.path.basename(code.co_filename), frame.f_lineno)
        self._self_acquired_at: int = 0
        self._self_name: Optional[str] = None

    def __aenter__(self, *args: Any, **kwargs: Any) -> Any:
        return self._acquire(self.__wrapped__.__aenter__, *args, **kwargs)

    def __aexit__(self, *args: Any, **kwargs: Any) -> Any:
        return self._release(self.__wrapped__.__aexit__, *args, **kwargs)

    def _acquire(self, inner_func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if not self._self_capture_sampler.capture():
            return inner_func(*args, **kwargs)

        start: int = time.monotonic_ns()
        try:
            return inner_func(*args, **kwargs)
        finally:
            try:
                end: int = time.monotonic_ns()
                self._self_acquired_at = end

                thread_id: int
                thread_name: str
                thread_id, thread_name = _current_thread()

                task_id: Optional[int]
                task_name: Optional[str]
                task_frame: Optional[FrameType]
                task_id, task_name, task_frame = _task.get_task(thread_id)

                self._maybe_update_self_name()
                lock_name: str = (
                    "%s:%s" % (self._self_init_loc, self._self_name) if self._self_name else self._self_init_loc
                )

                frame: FrameType
                if task_frame is None:
                    # If we can't get the task frame, we use the caller frame. We expect acquire/release or
                    # __enter__/__exit__ to be on the stack, so we go back 2 frames.
                    frame = sys._getframe(2)
                else:
                    frame = task_frame

                frames: List[DDFrame]
                frames, _ = _traceback.pyframe_to_frames(frame, self._self_max_nframes)

                thread_native_id: int = _threading.get_thread_native_id(thread_id)

                handle: ddup.SampleHandle = ddup.SampleHandle()
                handle.push_monotonic_ns(end)
                handle.push_lock_name(lock_name)
                handle.push_acquire(end - start, 1)  # AFAICT, capture_pct does not adjust anything here
                handle.push_threadinfo(thread_id, thread_native_id, thread_name)
                handle.push_task_id(task_id)
                handle.push_task_name(task_name)

                if self._self_tracer is not None:
                    handle.push_span(self._self_tracer.current_span())
                for ddframe in frames:
                    handle.push_frame(ddframe.function_name, ddframe.file_name, 0, ddframe.lineno)
                handle.flush_sample()
            except Exception:
                pass  # nosec

    def acquire(self, *args: Any, **kwargs: Any) -> Any:
        return self._acquire(self.__wrapped__.acquire, *args, **kwargs)

    def _release(self, inner_func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        # Using __slots__ makes attribute handling cleaner than with wrapt.ObjectProxy
        start: Optional[int] = getattr(self, "_self_acquired_at", None)
        try:
            # Though it should generally be avoided to call release() from
            # multiple threads, it is possible to do so. In that scenario, the
            # following statement code will raise an AttributeError. This should
            # not be propagated to the caller and to the users. The inner_func
            # will raise an RuntimeError as the threads are trying to release()
            # and unlocked lock, and the expected behavior is to propagate that.
            del self._self_acquired_at
        except AttributeError:
            # We just ignore the error, if the attribute is not found.
            pass
        try:
            return inner_func(*args, **kwargs)
        finally:
            if start is not None:
                end: int = time.monotonic_ns()

                thread_id: int
                thread_name: str
                thread_id, thread_name = _current_thread()

                task_id: Optional[int]
                task_name: Optional[str]
                task_frame: Optional[FrameType]
                task_id, task_name, task_frame = _task.get_task(thread_id)

                lock_name: str = (
                    "%s:%s" % (self._self_init_loc, self._self_name) if self._self_name else self._self_init_loc
                )

                frame: FrameType
                if task_frame is None:
                    # See the comments in _acquire
                    frame = sys._getframe(2)
                else:
                    frame = task_frame

                frames: List[DDFrame]
                frames, _ = _traceback.pyframe_to_frames(frame, self._self_max_nframes)

                thread_native_id: int = _threading.get_thread_native_id(thread_id)

                handle: ddup.SampleHandle = ddup.SampleHandle()
                handle.push_monotonic_ns(end)
                handle.push_lock_name(lock_name)
                handle.push_release(end - start, 1)  # AFAICT, capture_pct does not adjust anything here
                handle.push_threadinfo(thread_id, thread_native_id, thread_name)
                handle.push_task_id(task_id)
                handle.push_task_name(task_name)

                if self._self_tracer is not None:
                    handle.push_span(self._self_tracer.current_span())
                for ddframe in frames:
                    handle.push_frame(ddframe.function_name, ddframe.file_name, 0, ddframe.lineno)
                handle.flush_sample()

    def release(self, *args: Any, **kwargs: Any) -> Any:
        return self._release(self.__wrapped__.release, *args, **kwargs)

    def __enter__(self, *args: Any, **kwargs: Any) -> Any:
        return self._acquire(self.__wrapped__.__enter__, *args, **kwargs)

    def __exit__(self, *args: Any, **kwargs: Any) -> None:
        self._release(self.__wrapped__.__exit__, *args, **kwargs)

    def _find_self_name(self, var_dict: Dict[str, Any]) -> Optional[str]:
        for name, value in var_dict.items():
            if name.startswith("__") or isinstance(value, ModuleType):
                continue
            if value is self:
                return name
            if config.lock.name_inspect_dir:
                for attribute in dir(value):
                    if not attribute.startswith("__") and getattr(value, attribute) is self:
                        self._self_name = attribute
                        return attribute
        return None

    # Get lock acquire/release call location and variable name the lock is assigned to
    # This function propagates ValueError if the frame depth is <= 3.
    def _maybe_update_self_name(self) -> None:
        if self._self_name is not None:
            return
        # We expect the call stack to be like this:
        # 0: this
        # 1: _acquire/_release
        # 2: acquire/release (or __enter__/__exit__)
        # 3: caller frame
        if config.enable_asserts:
            frame: FrameType = sys._getframe(1)
            # TODO: replace dict with list
            if frame.f_code.co_name not in {"_acquire", "_release"}:
                raise AssertionError("Unexpected frame %s" % frame.f_code.co_name)
            frame = sys._getframe(2)
            if frame.f_code.co_name not in {
                "acquire",
                "release",
                "__enter__",
                "__exit__",
                "__aenter__",
                "__aexit__",
            }:
                raise AssertionError("Unexpected frame %s" % frame.f_code.co_name)
        frame = sys._getframe(3)

        # First, look at the local variables of the caller frame, and then the global variables
        self._self_name = self._find_self_name(frame.f_locals) or self._find_self_name(frame.f_globals)

        if not self._self_name:
            self._self_name = ""
    
    # Delegate remaining lock methods to the wrapped lock
    def locked(self) -> bool:
        """Return True if lock is currently held."""
        return self.__wrapped__.locked()
    
    def __repr__(self) -> str:
        return f"<_ProfiledLock({self.__wrapped__!r}) at {self._self_init_loc}>"
    
    # Support for being used in with statements
    def __bool__(self) -> bool:
        return True


class _LockAllocatorWrapper:
    """Wrapper for lock allocator functions that prevents method binding.
    
    When a function is stored as a class attribute and accessed via an instance,
    Python's descriptor protocol normally binds it as a method. This wrapper
    prevents that behavior by implementing __get__ to always return self,
    similar to how staticmethod works, but as a callable object.
    """
    
    __slots__ = ("_func",)
    
    def __init__(self, func: Callable[..., Any]) -> None:
        self._func: Callable[..., Any] = func
    
    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._func(*args, **kwargs)
    
    def __get__(self, instance: Any, owner: Optional[Type] = None) -> "_LockAllocatorWrapper":
        # Always return self, never bind as a method
        return self


class LockCollector(collector.CaptureSamplerCollector):
    """Record lock usage."""

    PROFILED_LOCK_CLASS: Type[Any]

    def __init__(
        self,
        nframes: int = config.max_frames,
        endpoint_collection_enabled: bool = config.endpoint_collection,
        tracer: Optional[Tracer] = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.nframes: int = nframes
        self.endpoint_collection_enabled: bool = endpoint_collection_enabled
        self.tracer: Optional[Tracer] = tracer
        self._original: Optional[Any] = None

    @abc.abstractmethod
    def _get_patch_target(self) -> Callable[..., Any]:
        ...

    @abc.abstractmethod
    def _set_patch_target(self, value: Any) -> None:
        ...

    def _start_service(self) -> None:
        """Start collecting lock usage."""
        self.patch()
        super(LockCollector, self)._start_service()  # type: ignore[safe-super]

    def _stop_service(self) -> None:
        """Stop collecting lock usage."""
        super(LockCollector, self)._stop_service()  # type: ignore[safe-super]
        self.unpatch()

    def patch(self) -> None:
        """Patch the module for tracking lock allocation."""
        # We only patch the lock from the `threading` module.
        # Nobody should use locks from `_thread`; if they do so, then it's deliberate and we don't profile.
        self._original = self._get_patch_target()

        # Create a simple wrapper function that returns profiled locks
        def _profiled_allocate_lock(*args: Any, **kwargs: Any) -> _ProfiledLock:
            lock: Any = self._original(*args, **kwargs)
            return self.PROFILED_LOCK_CLASS(
                lock,
                self.tracer,
                self.nframes,
                self._capture_sampler,
                self.endpoint_collection_enabled,
            )

        # Wrap the function to prevent it from being bound as a method when
        # accessed as a class attribute (e.g., Foo.lock_class = threading.Lock)
        self._set_patch_target(_LockAllocatorWrapper(_profiled_allocate_lock))

    def unpatch(self) -> None:
        """Unpatch the threading module for tracking lock allocation."""
        self._set_patch_target(self._original)
