# cython: annotation_typing=False
# ^^^ Prevents Cython from interpreting Python type annotations (e.g., `x: int`, `-> str`)
# as C type declarations. Without this, annotations like `Optional[str]` or `Callable[..., Any]`
# would cause Cython compilation errors since they aren't valid C types.

import _thread
import os.path
import sys
import time
from types import CodeType
from types import FrameType
from types import ModuleType
from types import TracebackType
from typing import Any
from typing import Callable
from typing import Optional
from typing import Union
from typing import cast

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.settings.profiling import config
from ddtrace.profiling import collector
from ddtrace.trace import Tracer

from ddtrace.profiling._threading import get_thread_name, get_thread_native_id
from ddtrace.profiling.collector._sampler cimport CaptureSampler
from ddtrace.profiling.collector._task cimport (
    get_task as _c_get_task,
    initialize_gevent_support as _c_initialize_gevent_support,
)
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


ACQUIRE_RELEASE_CO_NAMES: frozenset[str] = frozenset(["_acquire", "_release"])
ENTER_EXIT_CO_NAMES: frozenset[str] = frozenset(
    ["acquire", "release", "__enter__", "__exit__", "__aenter__", "__aexit__"]
)

# Cython-compiled def/cpdef functions do not create Python frame objects
# visible to sys._getframe(). All intermediate calls within this module
# (_acquire -> _flush_sample, __call__ -> _profiled_allocate_lock -> __init__)
# are invisible, so the caller's frame is at index 0, not 3.
cdef int _CALLER_FRAME_INDEX = 0


cdef tuple _current_thread():
    thread_id: int = _thread.get_ident()
    return thread_id, get_thread_name(thread_id)


def _get_original_lock_class(module_name: str, class_name: str) -> Callable[..., Any]:
    """Reconstruct the original lock class when unpickling. Since this is a module-level function, it can be pickled."""
    import importlib

    module: ModuleType = importlib.import_module(module_name)
    obj: Union[_LockAllocatorWrapper, Callable[..., Any]] = getattr(module, class_name)

    # If the object is still wrapped (profiling active in this process), unwrap it. Else, return the original object.
    if isinstance(obj, _LockAllocatorWrapper) and obj._original_class:
        return obj._original_class

    return obj


def _create_original_lock_instance(module_name: str, class_name: str) -> Any:
    """Create an instance of the original lock class when unpickling a _ProfiledLock."""
    # Map internal _thread types to their threading module counterparts, as they're not directly accessible.
    if module_name == "_thread":
        if class_name == "lock":
            module_name, class_name = "threading", "Lock"
        elif class_name == "RLock":
            module_name, class_name = "threading", "RLock"

    lock_class: Callable[..., Any] = _get_original_lock_class(module_name, class_name)
    return lock_class()


class _ProfiledLock:
    """
    Lightweight lock wrapper that profiles lock acquire/release operations.
    It intercepts lock methods without the overhead of a full proxy object.
    """

    __slots__ = (
        "__wrapped__",
        "tracer",
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
        capture_sampler: collector.CaptureSampler,
        is_internal: bool = False,
    ) -> None:
        self.__wrapped__: Any = wrapped
        self.tracer: Optional[Tracer] = tracer
        self.capture_sampler: collector.CaptureSampler = capture_sampler
        # In Cython, intermediate frames are invisible to sys._getframe(); caller is at index 0
        try:
            frame: FrameType = sys._getframe(_CALLER_FRAME_INDEX)
        except ValueError:
            # Shallow call stacks can happen in edge cases (e.g., interpreter bootstrap).
            if config.enable_asserts:
                raise
            self.init_location = "unknown:0"
        else:
            code: CodeType = frame.f_code
            self.init_location = "%s:%d" % (os.path.basename(code.co_filename), frame.f_lineno)
        self.acquired_time: Optional[int] = None
        self.name: Optional[str] = None
        # If True, this lock is internal to another sync primitive (e.g., Lock inside Semaphore)
        # and should not generate profile samples to avoid double-counting
        self.is_internal: bool = is_internal

    # gevent compatibility — gevent's _patch_existing_locks calls
    # type(threading.RLock()) to determine the RLock type, then scans gc.get_objects()
    # for instances of that type. When our wrapper is on threading.RLock, the type is
    # _ProfiledLock, so ALL profiled lock instances match. gevent then checks each for
    # _owner/_RLock__owner; without this property, non-RLock wrappers (e.g. Lock) fail
    # the check, causing AssertionError("Found unknown lock implementation").
    @property
    def _owner(self) -> int | None:
        return getattr(self.__wrapped__, "_owner", None)

    @_owner.setter
    def _owner(self, value: int) -> None:
        try:
            self.__wrapped__._owner = value
        except (AttributeError, TypeError):
            pass

    # DUNDER methods

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _ProfiledLock):
            return bool(self.__wrapped__ == other.__wrapped__)
        return bool(self.__wrapped__ == other)

    def __getattr__(self, name: str) -> Any:
        # Delegates acquire_lock, release_lock, locked_lock, and any future methods
        return getattr(self.__wrapped__, name)

    def __hash__(self) -> int:
        return hash(self.__wrapped__)

    def __repr__(self) -> str:
        return "<_ProfiledLock(%r) at %s>" % (self.__wrapped__, self.init_location)

    def __reduce__(self) -> tuple[Callable[[str, str], Any], tuple[str, str]]:
        """Support pickling by returning the wrapped lock.

        In the context of multiprocessing, the child process will get the unwrapped lock class, which will be re-wrapped
        if profiling is enabled there.
        """
        wrapped_type: type[Any] = type(self.__wrapped__)
        return (
            _create_original_lock_instance,
            (wrapped_type.__module__, wrapped_type.__qualname__),
        )

    # Regular methods

    def locked(self) -> bool:
        """Return True if lock is currently held."""
        return bool(self.__wrapped__.locked())

    # The sampler.capture() check is intentionally duplicated across acquire, __enter__, and
    # __aenter__ rather than extracted into a shared helper. This eliminates one function-call
    # frame on the unsampled hot path, which fires on every lock operation. A helper would
    # reintroduce that overhead. Do NOT refactor. See:
    # test_unsampled_acquire_bypasses_inner_acquire / test_sampled_acquire_calls_inner_acquire.
    def acquire(self, *args: Any, **kwargs: Any) -> Any:
        cdef CaptureSampler sampler = <CaptureSampler>self.capture_sampler
        if not sampler.capture():
            if config.enable_asserts and type(self.__wrapped__).__name__ == "RLock":
                # For RLock, a re-entrant acquire can arrive here while a previous sampled acquire
                # is still in progress (acquired_time set). If so, the next release will emit a
                # sample whose hold time is truncated at this release rather than the true outer
                # release — the outer hold time is silently lost, skewing the "lock held" time metrics.
                assert self.acquired_time is None, (
                    "Unsampled re-entrant acquire while a sampled hold is in progress; "
                    "the next release will emit a truncated hold-time sample. acquired_time=%r" % (self.acquired_time,)
                )  # nosec
            return self.__wrapped__.acquire(*args, **kwargs)
        return self._acquire(self.__wrapped__.acquire, *args, **kwargs)

    def __enter__(self, *args: Any, **kwargs: Any) -> Any:
        cdef CaptureSampler sampler = <CaptureSampler>self.capture_sampler
        if not sampler.capture():
            if config.enable_asserts and type(self.__wrapped__).__name__ == "RLock":
                assert self.acquired_time is None, (
                    "Unsampled re-entrant acquire while a sampled hold is in progress; "
                    "the next release will emit a truncated hold-time sample. acquired_time=%r" % (self.acquired_time,)
                )  # nosec
            return self.__wrapped__.__enter__(*args, **kwargs)
        return self._acquire(self.__wrapped__.__enter__, *args, **kwargs)

    def __aenter__(self, *args: Any, **kwargs: Any) -> Any:
        cdef CaptureSampler sampler = <CaptureSampler>self.capture_sampler
        if not sampler.capture():
            if config.enable_asserts and type(self.__wrapped__).__name__ == "RLock":
                assert self.acquired_time is None, (
                    "Unsampled re-entrant acquire while a sampled hold is in progress; "
                    "the next release will emit a truncated hold-time sample. acquired_time=%r" % (self.acquired_time,)
                )  # nosec
            return self.__wrapped__.__aenter__(*args, **kwargs)
        return self._acquire(self.__wrapped__.__aenter__, *args, **kwargs)

    def _acquire(self, inner_func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        cdef long long start = time.monotonic_ns()
        result: Any = None
        error_info: Optional[tuple[BaseException, Optional[TracebackType]]] = None
        try:
            result = inner_func(*args, **kwargs)
        except BaseException as exc:
            error_info = (exc, exc.__traceback__)

        if result is False and error_info is None:
            return result

        cdef long long end = time.monotonic_ns()
        self.acquired_time = end
        if not self.is_internal:
            try:
                self._update_name()
                self._flush_sample(start, end, True)
            except AssertionError:
                if config.enable_asserts:
                    raise
            except Exception:
                # Instrumentation must never crash user code
                pass  # nosec
        if error_info is not None:
            err: BaseException
            tb: Optional[TracebackType]
            err, tb = error_info
            raise err.with_traceback(tb)

        return result

    def release(self, *args: Any, **kwargs: Any) -> Any:
        if self.acquired_time is None:
            return self.__wrapped__.release(*args, **kwargs)
        return self._release(self.__wrapped__.release, *args, **kwargs)

    def __exit__(self, *args: Any, **kwargs: Any) -> None:
        if self.acquired_time is None:
            self.__wrapped__.__exit__(*args, **kwargs)
            return
        self._release(self.__wrapped__.__exit__, *args, **kwargs)

    def __aexit__(self, *args: Any, **kwargs: Any) -> Any:
        if self.acquired_time is None:
            return self.__wrapped__.__aexit__(*args, **kwargs)
        return self._release(self.__wrapped__.__aexit__, *args, **kwargs)

    def _release(self, inner_func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        # `acquired_time` is guaranteed non-None by all callers, but we check it here anyway to be defensive.
        if self.acquired_time is None:
            return inner_func(*args, **kwargs)

        cdef long long start = self.acquired_time
        self.acquired_time = None

        # Note: this can raise an exception. We don't catch it because we
        # want to be transparent about errors (for obvious reasons) and we
        # don't need to "look" at the errors because we're not capturing them
        # anywhere.
        # What comes next in the function is only code for sampling the lock
        # release, so it is irrelevant if it fails.
        result = inner_func(*args, **kwargs)

        if self.is_internal:
            return result

        try:
            self._flush_sample(start, time.monotonic_ns(), False)
        except AssertionError:
            if config.enable_asserts:
                raise
        except Exception:
            # Instrumentation must never crash user code
            pass  # nosec

        return result

    def _flush_sample(self, long long start, long long end, bint is_acquire) -> None:
        """Push lock profiling data to ddup."""
        # Skip profiling for internal locks (e.g., Lock inside Semaphore/Condition)
        # to avoid double-counting when multiple collectors are active
        if self.is_internal:
            return

        cdef long long duration_ns = end - start
        try:
            handle: ddup.SampleHandle = ddup.SampleHandle()

            handle.push_monotonic_ns(end)

            lock_name: str = "%s:%s" % (self.init_location, self.name) if self.name else self.init_location
            handle.push_lock_name(lock_name)

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
            task_id, task_name, task_frame = _c_get_task()

            handle.push_task_id(task_id)
            handle.push_task_name(task_name)

            thread_native_id: int = get_thread_native_id(thread_id)
            handle.push_threadinfo(thread_id, thread_native_id, thread_name)

            if self.tracer is not None:
                handle.push_span(self.tracer.current_span())

            # If we can't get the task frame, we use the caller frame.
            # Call stack: 0: _flush_sample, 1: _acquire/_release, 2: acquire/release/__enter__/__exit__, 3: caller
            frame: FrameType = task_frame or sys._getframe(_CALLER_FRAME_INDEX)
            handle.push_pyframes(frame)

            handle.flush_sample()
        except AssertionError:
            if config.enable_asserts:
                raise
        except Exception:
            # Instrumentation must never crash user code
            pass  # nosec

    def _find_name(self, var_dict: dict[str, Any]) -> Optional[str]:
        cdef str name
        cdef str attribute
        cdef object value
        cdef object instance_dict
        cdef object klass
        cdef object attr_val
        for name, value in var_dict.items():
            if name.startswith("__") or isinstance(value, ModuleType):
                continue

            if value is self:
                return name

            if config.lock.name_inspect_dir:
                # Use __dict__ rather than dir + getattr to avoid invoking
                # arbitrary descriptors. Some third-party descriptors (e.g. Ray's get_actor_name)
                # crash the process when accessed from a non-actor worker context.
                instance_dict = getattr(value, "__dict__", None)
                if instance_dict is None:
                    # No __dict__ (built-in or __slots__-only). Slot attributes are backed
                    # by C-level member_descriptors, not arbitrary user descriptors, so
                    # getattr is safe here.
                    for klass in type(value).__mro__:
                        available_attributes = (
                            a for a in getattr(klass, "__slots__", ()) if not a.startswith("__")
                        )
                        for attribute in available_attributes:
                            if getattr(value, attribute, None) is self:
                                return attribute

                    continue

                for attribute, attr_val in instance_dict.items():
                    if not attribute.startswith("__") and attr_val is self:
                        return attribute
        return None

    def _update_name(self) -> None:
        """Get lock variable name from the caller's frame."""
        if self.name is not None:
            return

        try:
            # In Cython, intermediate frames (_acquire, acquire, etc.) are invisible to
            # sys._getframe(), so the caller frame is at index 0.
            frame: FrameType = sys._getframe(_CALLER_FRAME_INDEX)
            self.name = self._find_name(frame.f_locals) or self._find_name(frame.f_globals) or ""
        except AssertionError:
            if config.enable_asserts:
                raise
        except Exception:
            # Instrumentation must never crash user code
            pass  # nosec


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
        self._func: Callable[..., Any]
        self._original_class: Optional[type[Any]]
        if args:
            self._func = args[0]
            self._original_class = kwargs.get("original_class") or (args[1] if len(args) > 1 else None)
        else:
            self._func = kwargs.get("func")  # type: ignore[assignment]
            self._original_class = kwargs.get("original_class")

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._func(*args, **kwargs)

    def __get__(self, instance: Any, owner: Optional[type[Any]] = None) -> _LockAllocatorWrapper:
        # Prevent automatic method binding (e.g., Foo.lock_class = threading.Lock)
        return self

    def __getattr__(self, name: str) -> Any:
        # Delegate attribute access to the original class.
        # This is needed for Semaphore/BoundedSemaphore inheritance where code accesses
        # Semaphore.__init__ c-tor through our wrapper.
        original_class: Optional[type[Any]] = object.__getattribute__(self, "_original_class")
        if original_class is not None:
            return getattr(original_class, name)
        raise AttributeError("'%s' object has no attribute '%s'" % (type(self).__name__, name))

    def __or__(self, other: Optional[type[Any]]) -> Any:
        """Support PEP 604 type union syntax (e.g., asyncio.Condition | None)."""
        return (self._original_class | other) if isinstance(self._original_class, type) else NotImplemented

    def __ror__(self, other: Optional[type[Any]]) -> Any:
        """Support PEP 604 type union syntax (e.g., None | asyncio.Condition)."""
        return (other | self._original_class) if isinstance(self._original_class, type) else NotImplemented

    def __mro_entries__(self, bases: tuple[Any, ...]) -> tuple[type[Any], ...]:
        """Support subclassing the wrapped lock type (PEP 560).

        When custom lock types inherit from a wrapped lock
        (e.g. neo4j's AsyncRLock that inherits from asyncio.Lock), program error with:
        > TypeError: _LockAllocatorWrapper.__init__() takes 2 positional arguments but 4 were given

        This method returns the actual object type to be used as the base class.
        """
        return (self._original_class,)  # type: ignore[return-value]

    def __reduce__(self) -> tuple[Callable[[str, str], Callable[..., Any]], tuple[str, str]]:
        """Support pickling by returning the original class.

        In the context of multiprocessing, the child process will get the unwrapped lock class, which will be re-wrapped
        if profiling is enabled there.
        """
        if self._original_class is None:
            raise TypeError("Cannot pickle uninitialized _LockAllocatorWrapper")

        return (
            _get_original_lock_class,
            (self._original_class.__module__, self._original_class.__qualname__),
        )


class LockCollector(collector.CaptureSamplerCollector):
    """Record lock usage."""

    PROFILED_LOCK_CLASS: type[_ProfiledLock]
    MODULE: ModuleType  # e.g., threading module
    PATCHED_LOCK_NAME: str  # e.g., "Lock", "RLock", "Semaphore"
    # Module file to check for internal lock detection (e.g., threading.__file__ or asyncio.locks.__file__)
    # If None, defaults to threading.__file__ for backward compatibility
    INTERNAL_MODULE_FILE: Optional[str] = None

    def __init__(
        self,
        tracer: Optional[Tracer] = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.tracer: Optional[Tracer] = tracer
        self._original_lock: Optional[Callable[..., Any]] = None
        self._reimport_hook: Optional[Callable[[ModuleType], None]] = None

    def _get_patch_target(self) -> Callable[..., Any]:
        return cast(Callable[..., Any], getattr(self.MODULE, self.PATCHED_LOCK_NAME))

    def _set_patch_target(self, value: Union[_LockAllocatorWrapper, Callable[..., Any], None]) -> None:
        setattr(self.MODULE, self.PATCHED_LOCK_NAME, value)

    def _start_service(self) -> None:
        """Start collecting lock usage."""
        _c_initialize_gevent_support()
        self.patch()

        # Register a hook to re-apply patches if the target module is
        # re-imported after cleanup_loaded_modules() discards it from sys.modules.
        # Without this, ddtrace-run + gevent installed = lock profiling silently broken.
        module_name: str = self.MODULE.__name__
        patched_module_id: int = id(self.MODULE)

        def _on_module_reimport(new_module: ModuleType) -> None:
            nonlocal patched_module_
            if id(new_module) == patched_module_id:
                return
            log.warning(
                (
                    "%s: target module %r was re-imported (id %#x -> %#x); "
                    "re-applying lock profiling patches. "
                    "This typically happens when gevent is installed and cleanup_loaded_modules() "
                    "discards the previously-patched module from sys.modules."
                ),
                type(self).__name__,
                module_name,
                patched_module_id,
                id(new_module),
            )
            self.unpatch()
            self.MODULE = new_module
            self.patch()
            patched_module_id = id(new_module)

        self._reimport_hook = _on_module_reimport
        ModuleWatchdog.register_module_hook(module_name, self._reimport_hook)

        super(LockCollector, self)._start_service()  # type: ignore[safe-super]

    def _stop_service(self) -> None:
        """Stop collecting lock usage."""
        super(LockCollector, self)._stop_service()  # type: ignore[safe-super]
        self.unpatch()
        if self._reimport_hook is not None:
            ModuleWatchdog.unregister_module_hook(self.MODULE.__name__, self._reimport_hook)
            self._reimport_hook = None

    def patch(self) -> None:
        """Patch the module for tracking lock allocation."""
        original_lock: Callable[..., Any] = self._get_patch_target()
        if isinstance(original_lock, _LockAllocatorWrapper):
            log.debug(
                "%s: %s.%s is already patched, skipping to avoid double-wrapping.",
                type(self).__name__,
                self.MODULE.__name__,
                self.PATCHED_LOCK_NAME,
            )
            return
        self._original_lock = original_lock

        # Determine which module file to check for internal lock detection
        internal_module_file: Optional[str] = self.INTERNAL_MODULE_FILE
        if internal_module_file is None:
            # Default to threading.__file__ for backward compatibility
            import threading as threading_module

            internal_module_file = threading_module.__file__

        # Precompute the resolved internal module path once — avoids repeated filesystem syscalls
        # on every lock allocation.
        precomputed_internal_file: Optional[str] = None
        if internal_module_file:
            try:
                precomputed_internal_file = os.path.normpath(os.path.realpath(internal_module_file))
            except OSError:
                pass

        # Precompute module exclusion structures once at patch time (not per lock creation).
        # Two structures for fast matching:
        #   exclude_exact  — frozenset for O(1) exact module name lookup
        #   exclude_dotted — tuple of "prefix." strings for str.startswith
        exclude_exact: frozenset[str] = config.lock.exclude_modules
        exclude_dotted: tuple[str, ...] = tuple(p + "." for p in exclude_exact)

        def _profiled_allocate_lock(*args: Any, **kwargs: Any) -> Any:
            """Simple wrapper that returns profiled locks.

            Detects if the lock is being created from within the stdlib module
            (i.e., internal to Semaphore/Condition) to avoid double-counting.
            Skips wrapping entirely for locks created from excluded modules.
            """
            cdef bint is_internal = False
            cdef str caller_filename
            cdef str caller_module
            try:
                # In Cython, intermediate frames are invisible; caller is at index 0
                caller_frame: FrameType = sys._getframe(0)
                caller_filename = caller_frame.f_code.co_filename

                # Module exclusion: return native lock with zero profiling overhead
                if exclude_exact:
                    caller_module = caller_frame.f_globals.get("__name__", "")
                    if caller_module in exclude_exact or caller_module.startswith(exclude_dotted):
                        return original_lock(*args, **kwargs)

                # Internal lock detection (e.g., threading.Semaphore creating a Lock internally)
                if precomputed_internal_file and caller_filename:
                    caller_filename = os.path.normpath(os.path.realpath(caller_filename))
                    is_internal = caller_filename == precomputed_internal_file
            except (ValueError, AttributeError, OSError):
                pass

            return self.PROFILED_LOCK_CLASS(
                wrapped=original_lock(*args, **kwargs),
                tracer=self.tracer,
                capture_sampler=self._capture_sampler,
                is_internal=is_internal,
            )

        self._set_patch_target(_LockAllocatorWrapper(_profiled_allocate_lock, original_class=original_lock))

    def unpatch(self) -> None:
        """Unpatch the threading module for tracking lock allocation."""
        self._set_patch_target(self._original_lock)
