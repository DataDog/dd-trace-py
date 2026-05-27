# cython: annotation_typing=False
# ^^^ Prevents Cython from interpreting Python type annotations (e.g., `x: int`, `-> str`)
# as C type declarations. Without this, annotations like `Optional[str]` or `Callable[..., Any]`
# would cause Cython compilation errors since they aren't valid C types.

from __future__ import annotations

import _thread
import functools
import os.path
import sys
import time
from types import CodeType
from types import FrameType
from types import ModuleType
from types import TracebackType
from typing import Any
from typing import Callable
from typing import ClassVar
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

# Modules whose internal lock allocations are always excluded from profiling,
# regardless of user config. Do NOT remove stdlib entries — they prevent double-counting when
# multiple collectors (Lock + Semaphore + Condition) are active simultaneously.
_ALWAYS_EXCLUDED_MODULES: frozenset = frozenset({
    "threading",
    "asyncio",
    "concurrent",
})

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
    )

    def __init__(
        self,
        wrapped: Any,
        tracer: Optional[Tracer],
        capture_sampler: collector.CaptureSampler,
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

    def acquire(self, *args: Any, **kwargs: Any) -> Any:
        return self._acquire(self.__wrapped__.acquire, *args, **kwargs)

    def __enter__(self, *args: Any, **kwargs: Any) -> Any:
        return self._acquire(self.__wrapped__.__enter__, *args, **kwargs)

    def __aenter__(self, *args: Any, **kwargs: Any) -> Any:
        return self._acquire(self.__wrapped__.__aenter__, *args, **kwargs)

    def _acquire(self, inner_func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        cdef CaptureSampler sampler = <CaptureSampler>self.capture_sampler
        if not sampler.capture():
            if config.enable_asserts:
                # Ensure acquired_time is not set when acquire is not sampled
                # (else a bogus release sample is produced)
                assert self.acquired_time is None, (
                    "Expected acquired_time to be None when acquire is not sampled, got %r" % (self.acquired_time,)
                )  # nosec

            return inner_func(*args, **kwargs)

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
        try:
            self._update_name()
            self._flush_sample(start, end, True)
        except AssertionError:
            if config.enable_asserts:
                raise
        except Exception:
            pass  # nosec
        if error_info is not None:
            err: BaseException
            tb: Optional[TracebackType]
            err, tb = error_info
            raise err.with_traceback(tb)

        return result

    def release(self, *args: Any, **kwargs: Any) -> Any:
        return self._release(self.__wrapped__.release, *args, **kwargs)

    def __exit__(self, *args: Any, **kwargs: Any) -> None:
        self._release(self.__wrapped__.__exit__, *args, **kwargs)

    def __aexit__(self, *args: Any, **kwargs: Any) -> Any:
        return self._release(self.__wrapped__.__aexit__, *args, **kwargs)

    def _release(self, inner_func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        start: Optional[int] = getattr(self, "acquired_time", None)
        self.acquired_time = None

        # Note: this can raise an exception. We don't catch it because we
        # want to be transparent about errors (for obvious reasons) and we
        # don't need to "look" at the errors because we're not capturing them
        # anywhere.
        # What comes next in the function is only code for sampling the lock
        # release, so it is irrelevant if it fails.
        result = inner_func(*args, **kwargs)

        if start is None:
            return result

        try:
            self._flush_sample(start, time.monotonic_ns(), False)
        except AssertionError:
            if config.enable_asserts:
                raise
        except Exception:
            pass  # nosec

        return result

    def _flush_sample(self, long long start, long long end, bint is_acquire) -> None:
        """Push lock profiling data to ddup."""
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

    _active_collectors: ClassVar[set[LockCollector]] = set()
    _gevent_hook_registered: ClassVar[bool] = False
    _gevent_monkey_wrapped: ClassVar[bool] = False

    @classmethod
    def _ensure_gevent_monkey_hook(cls) -> None:
        """Register a one-time hook to detect gevent monkey-patching.

        When gevent.monkey.patch_all or patch_thread runs, it replaces
        threading lock primitives in-place, destroying our _LockAllocatorWrapper.
        This hook wraps those functions so we can re-apply lock profiling afterwards.
        """
        if not cls._gevent_hook_registered:
            return

        cls._gevent_hook_registered = True

        if "gevent.monkey" in sys.modules:
            cls._wrap_gevent_monkey(sys.modules["gevent.monkey"])

        ModuleWatchdog.register_module_hook("gevent.monkey", cls._wrap_gevent_monkey)

    @classmethod
    def _wrap_gevent_monkey(cls, gevent_monkey: ModuleType) -> None:
        """Wrap gevent.monkey.patch_all and patch_thread with a post-callback."""
        if cls._gevent_monkey_wrapped:
            return

        cls._gevent_monkey_wrapped = True

        original_patch_all: Callable[..., Any] = gevent_monkey.patch_all
        original_patch_thread: Callable[..., Any] = gevent_monkey.patch_thread

        @functools.wraps(original_patch_all)
        def _wrapped_patch_all(*args: Any, **kwargs: Any) -> Any:
            result: Any = original_patch_all(*args, **kwargs)
            cls._repatch_after_gevent_monkey()
            return result

        @functools.wraps(original_patch_thread)
        def _wrapped_patch_thread(*args: Any, **kwargs: Any) -> Any:
            result: Any = original_patch_thread(*args, **kwargs)
            cls._repatch_after_gevent_monkey()
            return result

        gevent_monkey.patch_all = _wrapped_patch_all
        gevent_monkey.patch_thread = _wrapped_patch_thread

        if hasattr(gevent_monkey, "is_module_patched") and gevent_monkey.is_module_patched("threading"):
            cls._repatch_after_gevent_monkey()

    @classmethod
    def _repatch_after_gevent_monkey(cls) -> None:
        """Re-apply lock profiling patches on any collector whose wrapper was overwritten."""
        collector: LockCollector
        for collector in list(cls._active_collectors):
            current: Callable[..., Any] = collector._get_patch_target()
            if not isinstance(current, _LockAllocatorWrapper):
                log.debug(
                    "%s: gevent monkey-patching overwrote lock profiler wrapper on %s.%s; re-applying.",
                    type(collector).__name__,
                    collector.MODULE.__name__,
                    collector.PATCHED_LOCK_NAME,
                )
                collector.patch()

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

        LockCollector._active_collectors.add(self)
        LockCollector._ensure_gevent_monkey_hook()

        # Register a hook to re-apply patches if the target module is
        # re-imported after cleanup_loaded_modules() discards it from sys.modules.
        # Without this, ddtrace-run + gevent installed = lock profiling silently broken.
        module_name: str = self.MODULE.__name__
        patched_module_id: int = id(self.MODULE)

        def _on_module_reimport(new_module: ModuleType) -> None:
            nonlocal patched_module_id
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
        LockCollector._active_collectors.discard(self)
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

            # preserve for unpatch()
            self._original_lock = original_lock._original_class
            return
        self._original_lock = original_lock

        # Merge user-configured exclusions with the always-excluded stdlib modules.
        # _ALWAYS_EXCLUDED_MODULES prevents double-counting when multiple collectors
        # (Lock + Semaphore + Condition) are active.
        exclude_exact: frozenset[str] = (
            config.lock.exclude_modules | _ALWAYS_EXCLUDED_MODULES
        )
        exclude_dotted: tuple[str, ...] = tuple(p + "." for p in exclude_exact)

        def _profiled_allocate_lock(*args: Any, **kwargs: Any) -> Any:
            """Return a profiled lock, or a native lock for excluded modules."""
            cdef str caller_module
            try:
                caller_frame: FrameType = sys._getframe(0)
                caller_module = caller_frame.f_globals.get("__name__", "")
                if caller_module in exclude_exact or caller_module.startswith(exclude_dotted):
                    return original_lock(*args, **kwargs)
            except (ValueError, AttributeError, OSError):
                pass

            return self.PROFILED_LOCK_CLASS(
                wrapped=original_lock(*args, **kwargs),
                tracer=self.tracer,
                capture_sampler=self._capture_sampler,
            )

        self._set_patch_target(_LockAllocatorWrapper(_profiled_allocate_lock, original_class=original_lock))

    def unpatch(self) -> None:
        """Unpatch the threading module for tracking lock allocation."""
        self._set_patch_target(self._original_lock)
