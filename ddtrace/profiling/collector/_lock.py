from __future__ import absolute_import
from __future__ import annotations

import _thread
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
from ddtrace.internal.settings.profiling import config
from ddtrace.profiling import _threading
from ddtrace.profiling import collector
from ddtrace.profiling.collector import _task
from ddtrace.profiling.collector import _traceback
from ddtrace.profiling.event import DDFrame
from ddtrace.trace import Tracer


ACQUIRE_RELEASE_CO_NAMES: frozenset[str] = frozenset(["_acquire", "_release"])
ENTER_EXIT_CO_NAMES: frozenset[str] = frozenset(
    ["acquire", "release", "__enter__", "__exit__", "__aenter__", "__aexit__"]
)


def _current_thread() -> Tuple[int, str]:
    thread_id: int = _thread.get_ident()
    return thread_id, _threading.get_thread_name(thread_id)


class _ProfiledLock:
    """
    Lightweight lock wrapper that profiles lock acquire/release operations.
    It intercepts lock methods without the overhead of a full proxy object.
    """

    __slots__ = (
        "__wrapped__",
        "tracer",
        "max_nframes",
        "capture_sampler",
        "init_location",
        "acquired_time",
        "name",
        "is_internal",
    )

    def __init__(
        self,
        wrapped: Any,
        tracer: Optional[Tracer],
        max_nframes: int,
        capture_sampler: collector.CaptureSampler,
        is_internal: bool = False,
    ) -> None:
        self.__wrapped__: Any = wrapped
        self.tracer: Optional[Tracer] = tracer
        self.max_nframes: int = max_nframes
        self.capture_sampler: collector.CaptureSampler = capture_sampler
        # Frame depth: 0=__init__, 1=_profiled_allocate_lock, 2=_LockAllocatorWrapper.__call__, 3=caller
        frame: FrameType = sys._getframe(3)
        code: CodeType = frame.f_code
        self.init_location: str = f"{os.path.basename(code.co_filename)}:{frame.f_lineno}"
        self.acquired_time: Optional[int] = None
        self.name: Optional[str] = None
        # If True, this lock is internal to another sync primitive (e.g., Lock inside Semaphore)
        # and should not generate profile samples to avoid double-counting
        self.is_internal: bool = is_internal

    ### DUNDER methods ###

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, _ProfiledLock):
            return self.__wrapped__ == other.__wrapped__
        return self.__wrapped__ == other

    def __getattr__(self, name: str) -> Any:
        # Delegates acquire_lock, release_lock, locked_lock, and any future methods
        return getattr(self.__wrapped__, name)

    def __hash__(self) -> int:
        return hash(self.__wrapped__)

    def __repr__(self) -> str:
        return f"<_ProfiledLock({self.__wrapped__!r}) at {self.init_location}>"

    ### Regular methods ###

    def locked(self) -> bool:
        """Return True if lock is currently held."""
        return self.__wrapped__.locked()

    def acquire(self, *args: Any, **kwargs: Any) -> Any:
        return self._acquire(self.__wrapped__.acquire, *args, **kwargs)

    def __enter__(self, *args: Any, **kwargs: Any) -> Any:
        return self._acquire(self.__wrapped__.__enter__, *args, **kwargs)

    def __aenter__(self, *args: Any, **kwargs: Any) -> Any:
        return self._acquire(self.__wrapped__.__aenter__, *args, **kwargs)

    def _acquire(self, inner_func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if not self.capture_sampler.capture():
            if config.enable_asserts:
                # Ensure acquired_time is not set when acquire is not sampled
                # (else a bogus release sample is produced)
                assert self.acquired_time is None, (
                    f"Expected acquired_time to be None when acquire is not sampled, got {self.acquired_time!r}"
                )  # nosec

            return inner_func(*args, **kwargs)

        start: int = time.monotonic_ns()
        try:
            return inner_func(*args, **kwargs)
        finally:
            end: int = time.monotonic_ns()
            self.acquired_time = end
            try:
                self._update_name()
                self._flush_sample(start, end, is_acquire=True)
            except AssertionError:
                if config.enable_asserts:
                    # AssertionError exceptions need to propagate
                    raise
            except Exception:
                # Instrumentation must never crash user code
                pass  # nosec

    def release(self, *args: Any, **kwargs: Any) -> Any:
        return self._release(self.__wrapped__.release, *args, **kwargs)

    def __exit__(self, *args: Any, **kwargs: Any) -> None:
        self._release(self.__wrapped__.__exit__, *args, **kwargs)

    def __aexit__(self, *args: Any, **kwargs: Any) -> Any:
        return self._release(self.__wrapped__.__aexit__, *args, **kwargs)

    def _release(self, inner_func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        start: Optional[int] = getattr(self, "acquired_time", None)
        self.acquired_time = None

        try:
            return inner_func(*args, **kwargs)
        finally:
            if start:
                self._flush_sample(start, end=time.monotonic_ns(), is_acquire=False)

    def _flush_sample(self, start: int, end: int, is_acquire: bool) -> None:
        """Helper method to push lock profiling data to ddup.

        Args:
            start: Start timestamp in nanoseconds
            end: End timestamp in nanoseconds
            is_acquire: True for acquire operations, False for release operations
        """
        # Skip profiling for internal locks (e.g., Lock inside Semaphore/Condition)
        # to avoid double-counting when multiple collectors are active
        if self.is_internal:
            return

        handle: ddup.SampleHandle = ddup.SampleHandle()

        handle.push_monotonic_ns(end)

        lock_name: str = f"{self.init_location}:{self.name}" if self.name else self.init_location
        handle.push_lock_name(lock_name)

        duration_ns: int = end - start
        if is_acquire:
            handle.push_acquire(duration_ns, 1)
        else:
            handle.push_release(duration_ns, 1)

        thread_id: int
        thread_name: str
        thread_id, thread_name = _current_thread()

        task_id: Optional[int]
        task_name: Optional[str]
        task_frame: Optional[FrameType]
        task_id, task_name, task_frame = _task.get_task(thread_id)

        handle.push_task_id(task_id)
        handle.push_task_name(task_name)

        thread_native_id: int = _threading.get_thread_native_id(thread_id)
        handle.push_threadinfo(thread_id, thread_native_id, thread_name)

        if self.tracer is not None:
            handle.push_span(self.tracer.current_span())

        # If we can't get the task frame, we use the caller frame.
        # Call stack: 0: _flush_sample, 1: _acquire/_release, 2: acquire/release/__enter__/__exit__, 3: caller
        frame: FrameType = task_frame or sys._getframe(3)
        frames: List[DDFrame] = _traceback.pyframe_to_frames(frame, self.max_nframes)
        for ddframe in frames:
            handle.push_frame(ddframe.function_name, ddframe.file_name, 0, ddframe.lineno)

        handle.flush_sample()

    def _find_name(self, var_dict: Dict[str, Any]) -> Optional[str]:
        for name, value in var_dict.items():
            if name.startswith("__") or isinstance(value, ModuleType):
                continue
            if value is self:
                return name
            if config.lock.name_inspect_dir:
                for attribute in dir(value):
                    try:
                        if not attribute.startswith("__") and getattr(value, attribute) is self:
                            return attribute
                    except AttributeError:
                        # Accessing unset attributes in __slots__ raises AttributeError.
                        pass
        return None

    # Get lock acquire/release call location and variable name the lock is assigned to
    # This function propagates ValueError if the frame depth is <= 3.
    def _update_name(self) -> None:
        if self.name is not None:
            return
        # We expect the call stack to be like this:
        # 0: this
        # 1: _acquire/_release
        # 2: acquire/release (or __enter__/__exit__)
        # 3: caller frame
        if config.enable_asserts:
            frame: FrameType = sys._getframe(1)
            if frame.f_code.co_name not in ACQUIRE_RELEASE_CO_NAMES:
                raise AssertionError(f"Unexpected frame in stack: '{frame.f_code.co_name}'")

            frame = sys._getframe(2)
            if frame.f_code.co_name not in ENTER_EXIT_CO_NAMES:
                raise AssertionError(f"Unexpected frame in stack: '{frame.f_code.co_name}'")

        # First, look at the local variables of the caller frame, and then the global variables
        frame = sys._getframe(3)
        self.name = self._find_name(frame.f_locals) or self._find_name(frame.f_globals) or ""


class _LockAllocatorWrapper:
    """Wrapper for lock allocator functions that prevents method binding.

    For simple locks (Lock, RLock), this wrapper just intercepts instantiation.

    For class-based locks with inheritance (Semaphore, BoundedSemaphore), this wrapper
    also handles the case where a subclass calls Parent.__init__(self, value). Example:

        # In Python's threading.py:
        class BoundedSemaphore(Semaphore):
            def __init__(self, value=1):
                Semaphore.__init__(self, value)  # <-- We intercept this!
                self._initial_value = value

    When we patch threading.Semaphore with this wrapper, the call to Semaphore.__init__
    goes to our __init__, which detects the inheritance case and delegates to the
    original Semaphore.__init__.
    """

    __slots__ = ("_func", "_original_class")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # This __init__ handles TWO different cases:
        #
        # Case 1 - Normal wrapper initialization (most common):
        #   Called when setting up the profiling wrapper.
        #
        # Case 2 - Inheritance delegation (Semaphore/BoundedSemaphore only):
        #   We detect this by checking if args[0] is an instance of the original class.

        # Case 2: inheritance call where first arg is an instance being initialized
        # This happens when BoundedSemaphore.__init__ calls Semaphore.__init__(self, value)
        if args and hasattr(self, "_original_class") and self._original_class is not None:
            first_arg: Any = args[0]
            if isinstance(first_arg, self._original_class):
                # Delegate to the real Semaphore.__init__
                self._original_class.__init__(*args, **kwargs)
                return

        # Case 1: Normal wrapper initialization
        self._func: Callable[..., _ProfiledLock]
        self._original_class: Optional[Type[Any]]
        if args:
            self._func = args[0]
            self._original_class = kwargs.get("original_class") or (args[1] if len(args) > 1 else None)
        else:
            self._func = kwargs.get("func")  # type: ignore[assignment]
            self._original_class = kwargs.get("original_class")

    def __call__(self, *args: Any, **kwargs: Any) -> _ProfiledLock:
        return self._func(*args, **kwargs)

    def __get__(self, instance: Any, owner: Optional[Type[Any]] = None) -> _LockAllocatorWrapper:
        # Prevent automatic method binding (e.g., Foo.lock_class = threading.Lock)
        return self

    def __getattr__(self, name: str) -> Any:
        # Delegate attribute access to the original class.
        # This is needed for Semaphore/BoundedSemaphore inheritance where code accesses
        # Semaphore.__init__ c-tor through our wrapper.
        original_class: Optional[Type[Any]] = object.__getattribute__(self, "_original_class")
        if original_class is not None:
            return getattr(original_class, name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")


class LockCollector(collector.CaptureSamplerCollector):
    """Record lock usage."""

    PROFILED_LOCK_CLASS: Type[Any]
    MODULE: ModuleType  # e.g., threading module
    PATCHED_LOCK_NAME: str  # e.g., "Lock", "RLock", "Semaphore"

    def __init__(
        self,
        nframes: int = config.max_frames,
        tracer: Optional[Tracer] = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.nframes: int = nframes
        self.tracer: Optional[Tracer] = tracer
        self._original_lock: Any = None

    def _get_patch_target(self) -> Callable[..., Any]:
        return getattr(self.MODULE, self.PATCHED_LOCK_NAME)

    def _set_patch_target(self, value: Any) -> None:
        setattr(self.MODULE, self.PATCHED_LOCK_NAME, value)

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
        self._original_lock = self._get_patch_target()
        original_lock: Any = self._original_lock  # Capture non-None value

        def _profiled_allocate_lock(*args: Any, **kwargs: Any) -> _ProfiledLock:
            """Simple wrapper that returns profiled locks.

            Detects if the lock is being created from within threading.py stdlib
            (i.e., internal to Semaphore/Condition) to avoid double-counting.
            """
            import threading as threading_module

            # Check if caller is from threading.py (internal lock)
            is_internal: bool = False
            try:
                # Frame 0: _profiled_allocate_lock
                # Frame 1: _LockAllocatorWrapper.__call__
                # Frame 2: actual caller (threading.Lock() call site)
                caller_filename = sys._getframe(2).f_code.co_filename
                threading_module_file = threading_module.__file__
                if threading_module_file and caller_filename:
                    # Normalize paths to handle symlinks and different path formats
                    caller_filename_normalized = os.path.normpath(os.path.realpath(caller_filename))
                    threading_file_normalized = os.path.normpath(os.path.realpath(threading_module_file))
                    is_internal = caller_filename_normalized == threading_file_normalized
            except (ValueError, AttributeError, OSError):
                pass

            return self.PROFILED_LOCK_CLASS(
                wrapped=original_lock(*args, **kwargs),
                tracer=self.tracer,
                max_nframes=self.nframes,
                capture_sampler=self._capture_sampler,
                is_internal=is_internal,
            )

        self._set_patch_target(_LockAllocatorWrapper(_profiled_allocate_lock, original_class=original_lock))

    def unpatch(self) -> None:
        """Unpatch the threading module for tracking lock allocation."""
        self._set_patch_target(self._original_lock)
