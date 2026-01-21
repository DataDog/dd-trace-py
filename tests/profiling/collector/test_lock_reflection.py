"""Reflection-based tests for Lock Profiler wrapper compatibility.

This module uses Python's introspection capabilities to automatically verify that
wrapped lock types are fully compatible with their originals. This catches interface
mismatches that are hard to predict manually, such as:

- PR #15604: Missing __mro_entries__ broke subclassing wrapped lock types
- PR #15899: Missing __reduce__ broke serialization of wrapped lock objects

The key insight is that when we wrap/replace standard library types, we must maintain
FULL protocol compliance - not just the obvious methods like acquire/release, but also:
- PEP 560 hooks (__mro_entries__, __class_getitem__)
- Pickling protocol (__reduce__, __reduce_ex__, __getstate__, __setstate__)
- Context manager protocol (__enter__, __exit__, __aenter__, __aexit__)
- Comparison protocol (__eq__, __hash__, __ne__)
- And any other dunder methods the original type supports

AIDEV-NOTE: This is a critical test module for catching wrapper compatibility bugs.
When adding new dunder methods to _ProfiledLock or _LockAllocatorWrapper, add
corresponding tests here.
"""

from __future__ import annotations

import asyncio
import pickle
import threading
from typing import Any
from typing import List
from typing import Optional
from typing import Protocol
from typing import Set
from typing import Tuple
from typing import Type
from typing import runtime_checkable

import pytest

from ddtrace.profiling.collector._lock import _LockAllocatorWrapper
from ddtrace.profiling.collector._lock import _ProfiledLock
from ddtrace.profiling.collector.asyncio import AsyncioBoundedSemaphoreCollector
from ddtrace.profiling.collector.asyncio import AsyncioConditionCollector
from ddtrace.profiling.collector.asyncio import AsyncioLockCollector
from ddtrace.profiling.collector.asyncio import AsyncioSemaphoreCollector
from ddtrace.profiling.collector.threading import ThreadingBoundedSemaphoreCollector
from ddtrace.profiling.collector.threading import ThreadingConditionCollector
from ddtrace.profiling.collector.threading import ThreadingLockCollector
from ddtrace.profiling.collector.threading import ThreadingRLockCollector
from ddtrace.profiling.collector.threading import ThreadingSemaphoreCollector
from tests.profiling.collector.test_utils import init_ddup


# =============================================================================
# Protocol Definitions - Define what a "Lock" must support
# =============================================================================


@runtime_checkable
class LockProtocol(Protocol):
    """Minimal protocol that all lock types must satisfy."""

    def acquire(self, *args: Any, **kwargs: Any) -> Any: ...
    def release(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class ContextManagerProtocol(Protocol):
    """Context manager protocol for synchronous locks."""

    def __enter__(self) -> Any: ...
    def __exit__(self, *args: Any) -> Any: ...


@runtime_checkable
class AsyncContextManagerProtocol(Protocol):
    """Async context manager protocol for asyncio locks."""

    async def __aenter__(self) -> Any: ...
    async def __aexit__(self, *args: Any) -> Any: ...


# =============================================================================
# Test Data - All lock types and their collectors
# =============================================================================


# Threading lock types with their collectors
THREADING_LOCK_CONFIGS: List[Tuple[Type[Any], Type[Any], str]] = [
    (threading.Lock, ThreadingLockCollector, "Lock"),
    (threading.RLock, ThreadingRLockCollector, "RLock"),
    (threading.Semaphore, ThreadingSemaphoreCollector, "Semaphore"),
    (threading.BoundedSemaphore, ThreadingBoundedSemaphoreCollector, "BoundedSemaphore"),
    (threading.Condition, ThreadingConditionCollector, "Condition"),
]

# Asyncio lock types with their collectors
ASYNCIO_LOCK_CONFIGS: List[Tuple[Type[Any], Type[Any], str]] = [
    (asyncio.Lock, AsyncioLockCollector, "Lock"),
    (asyncio.Semaphore, AsyncioSemaphoreCollector, "Semaphore"),
    (asyncio.BoundedSemaphore, AsyncioBoundedSemaphoreCollector, "BoundedSemaphore"),
    (asyncio.Condition, AsyncioConditionCollector, "Condition"),
]


# =============================================================================
# Helper Functions
# =============================================================================


def get_public_methods(obj: Any) -> Set[str]:
    """Get all public method names of an object.

    Note: This only finds methods visible in dir(). Methods delegated via
    __getattr__ won't appear here but are still accessible.
    """
    return {name for name in dir(obj) if not name.startswith("_") and callable(getattr(obj, name, None))}


def check_method_accessible(obj: Any, method_name: str) -> bool:
    """Check if a method is accessible on an object (even if via __getattr__)."""
    try:
        method = getattr(obj, method_name)
        return callable(method)
    except AttributeError:
        return False


def get_dunder_methods(cls: Type[Any]) -> Set[str]:
    """Get dunder method names that a class explicitly defines or overrides.

    This helps detect when a wrapper is missing a dunder that the original has.
    """
    # These are dunders that exist on all objects - we only care about ones
    # that the class explicitly overrides or adds
    base_object_dunders = {
        "__class__",
        "__delattr__",
        "__dir__",
        "__doc__",
        "__format__",
        "__getattribute__",
        "__init_subclass__",
        "__new__",
        "__reduce_ex__",
        "__setattr__",
        "__sizeof__",
        "__str__",
        "__subclasshook__",
    }

    cls_dunders = {name for name in dir(cls) if name.startswith("__") and name.endswith("__")}

    # Return dunders that are meaningful for lock behavior
    meaningful_dunders = cls_dunders - base_object_dunders
    return meaningful_dunders


def get_callable_dunders(obj: Any) -> Set[str]:
    """Get dunders that are callable (methods, not just attributes)."""
    return {
        name
        for name in dir(obj)
        if name.startswith("__") and name.endswith("__") and callable(getattr(obj, name, None))
    }


# =============================================================================
# Interface Compatibility Tests
# =============================================================================


class TestLockAllocatorWrapperInterface:
    """Test that _LockAllocatorWrapper maintains full interface compatibility.

    This class wraps lock allocator functions (like threading.Lock) and must
    support all the protocols that users might expect from the original.
    """

    @pytest.mark.parametrize("lock_class,collector_class,name", THREADING_LOCK_CONFIGS)
    def test_wrapper_exposes_original_class_dunders(
        self, lock_class: Type[Any], collector_class: Type[Any], name: str
    ) -> None:
        """Verify wrapper delegates attribute access to original class.

        This catches bugs like PR #15604 where __mro_entries__ was missing.
        """
        init_ddup(f"test_wrapper_dunders_{name}")

        with collector_class(capture_pct=100):
            wrapped = getattr(threading, name)
            assert isinstance(wrapped, _LockAllocatorWrapper)

            # Check critical dunders are accessible
            # __mro_entries__ is needed for PEP 560 subclassing
            assert hasattr(wrapped, "__mro_entries__"), f"{name} wrapper missing __mro_entries__ (PEP 560)"

            # __reduce__ is needed for pickling
            assert hasattr(wrapped, "__reduce__"), f"{name} wrapper missing __reduce__ (pickling)"

    @pytest.mark.parametrize("lock_class,collector_class,name", THREADING_LOCK_CONFIGS)
    def test_wrapper_is_callable(self, lock_class: Type[Any], collector_class: Type[Any], name: str) -> None:
        """Verify the wrapper can be called to create lock instances."""
        init_ddup(f"test_wrapper_callable_{name}")

        with collector_class(capture_pct=100):
            wrapped = getattr(threading, name)
            assert callable(wrapped), f"{name} wrapper should be callable"

            # Actually create an instance
            instance = wrapped()
            assert instance is not None

    @pytest.mark.parametrize("lock_class,collector_class,name", THREADING_LOCK_CONFIGS)
    def test_wrapper_get_descriptor_prevents_binding(
        self, lock_class: Type[Any], collector_class: Type[Any], name: str
    ) -> None:
        """Verify __get__ prevents method binding when used as class attribute.

        Some libraries do: class Foo: lock_class = threading.Lock
        The wrapper must not break this pattern.
        """
        init_ddup(f"test_wrapper_get_{name}")

        with collector_class(capture_pct=100):
            wrapped = getattr(threading, name)

            class Foo:
                lock_class = wrapped

            # Should return the wrapper, not a bound method
            assert Foo.lock_class is wrapped
            assert Foo().lock_class is wrapped

    @pytest.mark.parametrize(
        "lock_class,collector_class,name",
        [
            # Only test pure Python classes that support subclassing
            (threading.Semaphore, ThreadingSemaphoreCollector, "Semaphore"),
            (threading.BoundedSemaphore, ThreadingBoundedSemaphoreCollector, "BoundedSemaphore"),
            (threading.Condition, ThreadingConditionCollector, "Condition"),
        ],
    )
    def test_wrapper_supports_subclassing_pep560(
        self, lock_class: Type[Any], collector_class: Type[Any], name: str
    ) -> None:
        """Verify wrapper supports subclassing via PEP 560 __mro_entries__.

        This is the exact bug from PR #15604 - third-party libraries like neo4j
        define classes like: class AsyncRLock(asyncio.Lock): ...
        This must work even when asyncio.Lock is wrapped.
        """
        init_ddup(f"test_subclassing_{name}")

        with collector_class(capture_pct=100):
            wrapped = getattr(threading, name)
            assert isinstance(wrapped, _LockAllocatorWrapper)

            # This should NOT raise TypeError
            # Before PR #15604 fix, this would raise:
            # TypeError: _LockAllocatorWrapper.__init__() takes 2 positional arguments but 4 were given
            class CustomLock(wrapped):  # type: ignore[misc]
                def __init__(self, *args: Any, **kwargs: Any) -> None:
                    super().__init__(*args, **kwargs)
                    self.custom_attr = True

            # Verify subclass works correctly
            instance = CustomLock()
            assert hasattr(instance, "custom_attr")
            assert instance.custom_attr is True

            # Verify it still functions as a lock
            instance.acquire()
            instance.release()


class TestProfiledLockInterface:
    """Test that _ProfiledLock maintains full interface compatibility.

    When we wrap a lock instance, we must expose all methods and protocols
    that the original lock type supports.
    """

    @pytest.mark.parametrize("lock_class,collector_class,name", THREADING_LOCK_CONFIGS)
    def test_profiled_lock_has_all_public_methods(
        self, lock_class: Type[Any], collector_class: Type[Any], name: str
    ) -> None:
        """Verify wrapped lock exposes all public methods of the original.

        Note: Methods may be delegated via __getattr__ and not appear in dir(),
        but they should still be accessible via getattr().
        """
        init_ddup(f"test_public_methods_{name}")

        # Get methods from unwrapped instance
        original_instance = lock_class()
        original_methods = get_public_methods(original_instance)

        with collector_class(capture_pct=100):
            wrapped_class = getattr(threading, name)
            wrapped_instance = wrapped_class()

            # All original methods should be ACCESSIBLE on wrapped instance
            # (even if delegated via __getattr__ and not in dir())
            inaccessible_methods = {
                method for method in original_methods if not check_method_accessible(wrapped_instance, method)
            }
            assert not inaccessible_methods, (
                f"Wrapped {name} has inaccessible methods: {inaccessible_methods}. "
                f"Original has: {sorted(original_methods)}"
            )

    @pytest.mark.parametrize("lock_class,collector_class,name", THREADING_LOCK_CONFIGS)
    def test_profiled_lock_context_manager_protocol(
        self, lock_class: Type[Any], collector_class: Type[Any], name: str
    ) -> None:
        """Verify wrapped lock supports context manager protocol."""
        init_ddup(f"test_context_manager_{name}")

        with collector_class(capture_pct=100):
            wrapped_class = getattr(threading, name)
            lock = wrapped_class()

            # Must have __enter__ and __exit__
            assert hasattr(lock, "__enter__"), f"{name} missing __enter__"
            assert hasattr(lock, "__exit__"), f"{name} missing __exit__"

            # Must work with 'with' statement
            with lock:
                pass  # Should not raise

    @pytest.mark.parametrize("lock_class,collector_class,name", THREADING_LOCK_CONFIGS)
    def test_profiled_lock_comparison_protocol(
        self, lock_class: Type[Any], collector_class: Type[Any], name: str
    ) -> None:
        """Verify wrapped lock supports comparison and hashing protocols."""
        init_ddup(f"test_comparison_{name}")

        with collector_class(capture_pct=100):
            wrapped_class = getattr(threading, name)
            lock1 = wrapped_class()
            lock2 = wrapped_class()

            # __eq__ should work
            assert lock1 == lock1
            assert not (lock1 == lock2)  # Different locks

            # __hash__ should work (for use in sets/dicts)
            assert hash(lock1) == hash(lock1)
            lock_set = {lock1, lock2}
            assert len(lock_set) == 2

    @pytest.mark.parametrize("lock_class,collector_class,name", THREADING_LOCK_CONFIGS)
    def test_profiled_lock_repr(self, lock_class: Type[Any], collector_class: Type[Any], name: str) -> None:
        """Verify wrapped lock has informative __repr__."""
        init_ddup(f"test_repr_{name}")

        with collector_class(capture_pct=100):
            wrapped_class = getattr(threading, name)
            lock = wrapped_class()

            repr_str = repr(lock)
            assert repr_str is not None
            assert len(repr_str) > 0
            # Should mention it's a profiled lock and location
            assert "_ProfiledLock" in repr_str or "ProfiledLock" in repr_str


# =============================================================================
# Serialization/Pickling Tests (PR #15899)
# =============================================================================


class TestSerializationCompatibility:
    """Test that wrapped locks can be serialized/pickled.

    This is critical for multiprocessing scenarios where locks might be
    passed between processes. This catches bugs like PR #15899.
    """

    @pytest.mark.parametrize("lock_class,collector_class,name", THREADING_LOCK_CONFIGS)
    def test_wrapper_class_picklable(self, lock_class: Type[Any], collector_class: Type[Any], name: str) -> None:
        """Verify the wrapper class itself can be pickled.

        This is needed for Python 3.14+ multiprocessing with forkserver.
        """
        init_ddup(f"test_wrapper_pickle_{name}")

        with collector_class(capture_pct=100):
            wrapped = getattr(threading, name)
            assert isinstance(wrapped, _LockAllocatorWrapper)

            # Pickle and unpickle the wrapper class
            pickled = pickle.dumps(wrapped)
            unpickled = pickle.loads(pickled)

            # Unpickled should be the original class (unwrapped)
            # This is the expected behavior per the __reduce__ implementation
            assert unpickled is not None

    @pytest.mark.parametrize("lock_class,collector_class,name", THREADING_LOCK_CONFIGS)
    def test_profiled_lock_instance_picklable(
        self, lock_class: Type[Any], collector_class: Type[Any], name: str
    ) -> None:
        """Verify wrapped lock instances can be pickled.

        When pickling, we return an unwrapped lock (which gets re-wrapped
        if profiling is active in the unpickling process).
        """
        init_ddup(f"test_instance_pickle_{name}")

        with collector_class(capture_pct=100):
            wrapped_class = getattr(threading, name)
            lock = wrapped_class()
            assert isinstance(lock, _ProfiledLock)

            # Pickle and unpickle
            pickled = pickle.dumps(lock)
            unpickled = pickle.loads(pickled)

            # Unpickled should work as a lock
            assert unpickled is not None
            assert hasattr(unpickled, "acquire")
            assert hasattr(unpickled, "release")

    @pytest.mark.parametrize("lock_class,collector_class,name", THREADING_LOCK_CONFIGS)
    def test_reduce_returns_correct_structure(
        self, lock_class: Type[Any], collector_class: Type[Any], name: str
    ) -> None:
        """Verify __reduce__ returns proper tuple for pickle protocol."""
        init_ddup(f"test_reduce_{name}")

        with collector_class(capture_pct=100):
            wrapped_class = getattr(threading, name)
            lock = wrapped_class()
            assert isinstance(lock, _ProfiledLock)

            reduce_result = lock.__reduce__()

            # __reduce__ should return (callable, args) or (callable, args, state)
            assert isinstance(reduce_result, tuple)
            assert len(reduce_result) >= 2

            # First element should be callable
            assert callable(reduce_result[0])

            # Second element should be tuple of args
            assert isinstance(reduce_result[1], tuple)


# =============================================================================
# Behavioral Equivalence Tests
# =============================================================================


class TestBehavioralEquivalence:
    """Test that wrapped locks behave identically to unwrapped locks.

    This goes beyond interface checking to verify actual behavior matches.
    """

    @pytest.mark.parametrize("lock_class,collector_class,name", THREADING_LOCK_CONFIGS)
    def test_acquire_release_cycle(self, lock_class: Type[Any], collector_class: Type[Any], name: str) -> None:
        """Verify basic acquire/release works identically."""
        init_ddup(f"test_acquire_release_{name}")

        with collector_class(capture_pct=100):
            wrapped_class = getattr(threading, name)
            lock = wrapped_class()

            # Test blocking acquire
            result = lock.acquire()
            # acquire() returns True for Lock/RLock, None for others
            assert result in (True, None)

            lock.release()

    @pytest.mark.parametrize(
        "lock_class,collector_class,name",
        [
            (threading.Lock, ThreadingLockCollector, "Lock"),
            (threading.RLock, ThreadingRLockCollector, "RLock"),
            (threading.Semaphore, ThreadingSemaphoreCollector, "Semaphore"),
            (threading.BoundedSemaphore, ThreadingBoundedSemaphoreCollector, "BoundedSemaphore"),
        ],
    )
    def test_non_blocking_acquire(self, lock_class: Type[Any], collector_class: Type[Any], name: str) -> None:
        """Verify non-blocking acquire returns correct values."""
        init_ddup(f"test_non_blocking_{name}")

        with collector_class(capture_pct=100):
            wrapped_class = getattr(threading, name)
            lock = wrapped_class()

            # First non-blocking acquire should succeed
            result1 = lock.acquire(blocking=False)
            assert result1 is True

            # For Lock/Semaphore, second non-blocking acquire should fail
            if name in ("Lock", "Semaphore", "BoundedSemaphore"):
                result2 = lock.acquire(blocking=False)
                assert result2 is False

            lock.release()

    def test_locked_method(self) -> None:
        """Verify locked() method works correctly.

        Note: Only threading.Lock has a locked() method. RLock doesn't have it
        in Python < 3.13, and Semaphore/Condition don't have it at all.
        """
        init_ddup("test_locked_Lock")

        with ThreadingLockCollector(capture_pct=100):
            lock = threading.Lock()

            assert not lock.locked()
            lock.acquire()
            assert lock.locked()
            lock.release()
            assert not lock.locked()

    def test_bounded_semaphore_raises_on_over_release(self) -> None:
        """Verify BoundedSemaphore raises ValueError on over-release.

        This is a behavioral characteristic that must be preserved.
        """
        init_ddup("test_bounded_over_release")

        with ThreadingBoundedSemaphoreCollector(capture_pct=100):
            lock = threading.BoundedSemaphore(1)
            lock.acquire()
            lock.release()

            with pytest.raises(ValueError, match="Semaphore released too many times"):
                lock.release()


# =============================================================================
# Asyncio Tests
# =============================================================================


@pytest.mark.asyncio
class TestAsyncioLockReflection:
    """Reflection tests for asyncio lock wrappers."""

    @pytest.mark.parametrize("lock_class,collector_class,name", ASYNCIO_LOCK_CONFIGS)
    async def test_async_context_manager_protocol(
        self, lock_class: Type[Any], collector_class: Type[Any], name: str
    ) -> None:
        """Verify wrapped asyncio locks support async context manager protocol."""
        init_ddup(f"test_async_context_{name}")

        with collector_class(capture_pct=100):
            wrapped_class = getattr(asyncio, name)
            lock = wrapped_class()

            # Must have __aenter__ and __aexit__
            assert hasattr(lock, "__aenter__"), f"asyncio.{name} missing __aenter__"
            assert hasattr(lock, "__aexit__"), f"asyncio.{name} missing __aexit__"

            # Must work with 'async with' statement
            async with lock:
                pass  # Should not raise

    @pytest.mark.parametrize("lock_class,collector_class,name", ASYNCIO_LOCK_CONFIGS)
    async def test_async_subclassing_works(self, lock_class: Type[Any], collector_class: Type[Any], name: str) -> None:
        """Verify asyncio lock wrappers support subclassing (PEP 560).

        This is the exact bug from PR #15604 - neo4j's AsyncRLock inherits
        from asyncio.Lock.
        """
        init_ddup(f"test_async_subclass_{name}")

        with collector_class(capture_pct=100):
            wrapped_class = getattr(asyncio, name)
            assert isinstance(wrapped_class, _LockAllocatorWrapper)

            # This should NOT raise TypeError
            class CustomAsyncLock(wrapped_class):  # type: ignore[misc]
                def __init__(self, *args: Any, **kwargs: Any) -> None:
                    super().__init__(*args, **kwargs)
                    self.custom_attr = "test"

            # Verify subclass works
            instance = CustomAsyncLock()
            assert instance.custom_attr == "test"
            await instance.acquire()
            instance.release()


# =============================================================================
# Meta-Testing: Exhaustive Dunder Method Coverage
# =============================================================================


class TestDunderMethodCoverage:
    """Ensure all relevant dunder methods are implemented.

    This is a meta-test that checks we haven't forgotten any critical
    dunder methods that users might rely on.

    Uses reflection to automatically detect missing dunders rather than
    relying solely on hardcoded lists.
    """

    # Dunders that wrappers MUST implement (hardcoded baseline)
    REQUIRED_WRAPPER_DUNDERS = {
        "__call__",  # To create instances
        "__get__",  # Prevent method binding
        "__mro_entries__",  # PEP 560 subclassing
        "__reduce__",  # Pickling
        "__init__",  # Initialization
        "__getattr__",  # Delegate to original
    }

    # Dunders that profiled locks MUST implement (hardcoded baseline)
    REQUIRED_PROFILED_LOCK_DUNDERS = {
        "__enter__",  # Context manager
        "__exit__",  # Context manager
        "__eq__",  # Comparison
        "__hash__",  # For sets/dicts
        "__repr__",  # Debugging
        "__reduce__",  # Pickling
        "__getattr__",  # Delegate to wrapped
        "__init__",  # Initialization
    }

    # Dunders that profiled asyncio locks MUST additionally implement
    REQUIRED_ASYNC_DUNDERS = {
        "__aenter__",  # Async context manager
        "__aexit__",  # Async context manager
    }

    # Dunders we intentionally don't implement (they're not relevant for locks)
    INTENTIONALLY_MISSING = {
        "__class_getitem__",  # Generic type hints - not needed for locks
        "__init_subclass__",  # Subclass hooks - handled by __mro_entries__
        "__weakref__",  # Weak references - optional
        "__dict__",  # We use __slots__
        "__module__",  # Inherited from object
        "__doc__",  # Documentation
    }

    def test_lock_allocator_wrapper_has_required_dunders(self) -> None:
        """Verify _LockAllocatorWrapper has all required dunder methods."""
        wrapper_dunders = {name for name in dir(_LockAllocatorWrapper) if name.startswith("__") and name.endswith("__")}

        missing = self.REQUIRED_WRAPPER_DUNDERS - wrapper_dunders
        assert not missing, f"_LockAllocatorWrapper missing required dunders: {missing}"

    def test_profiled_lock_has_required_dunders(self) -> None:
        """Verify _ProfiledLock has all required dunder methods."""
        lock_dunders = {name for name in dir(_ProfiledLock) if name.startswith("__") and name.endswith("__")}

        missing = self.REQUIRED_PROFILED_LOCK_DUNDERS - lock_dunders
        assert not missing, f"_ProfiledLock missing required dunders: {missing}"

        # Also check async dunders
        missing_async = self.REQUIRED_ASYNC_DUNDERS - lock_dunders
        assert not missing_async, f"_ProfiledLock missing required async dunders: {missing_async}"

    @pytest.mark.parametrize("lock_class,collector_class,name", THREADING_LOCK_CONFIGS)
    def test_wrapped_lock_has_original_dunders(
        self, lock_class: Type[Any], collector_class: Type[Any], name: str
    ) -> None:
        """Automatically detect missing dunders by comparing with original.

        This uses reflection to find dunders on the original lock type and
        verify they're accessible on the wrapped version. This catches bugs
        like PR #15604 automatically.
        """
        init_ddup(f"test_dunders_{name}")

        # Get dunders from original
        original_instance = lock_class()
        original_dunders = get_callable_dunders(original_instance)

        with collector_class(capture_pct=100):
            wrapped_class = getattr(threading, name)
            wrapped_instance = wrapped_class()
            wrapped_dunders = get_callable_dunders(wrapped_instance)

            # Find dunders that are on original but missing from wrapped
            missing = original_dunders - wrapped_dunders - self.INTENTIONALLY_MISSING

            assert not missing, (
                f"Wrapped {name} missing dunders from original: {missing}. "
                f"If intentional, add to INTENTIONALLY_MISSING."
            )

    @pytest.mark.parametrize(
        "lock_class,collector_class,name",
        [
            # Only test pure Python classes that have meaningful dunders
            (threading.Semaphore, ThreadingSemaphoreCollector, "Semaphore"),
            (threading.BoundedSemaphore, ThreadingBoundedSemaphoreCollector, "BoundedSemaphore"),
            (threading.Condition, ThreadingConditionCollector, "Condition"),
        ],
    )
    def test_wrapper_class_dunders_match_original(
        self, lock_class: Type[Any], collector_class: Type[Any], name: str
    ) -> None:
        """Verify wrapper class exposes same dunders as original class.

        This checks the CLASS level (not instance), catching issues like
        missing __mro_entries__.
        """
        init_ddup(f"test_class_dunders_{name}")

        original_dunders = get_dunder_methods(lock_class)

        with collector_class(capture_pct=100):
            wrapped_class = getattr(threading, name)

            # The wrapper should expose dunders from the original class
            # via __getattr__ delegation, OR have them directly
            for dunder in original_dunders:
                if dunder in self.INTENTIONALLY_MISSING:
                    continue
                assert hasattr(wrapped_class, dunder), (
                    f"Wrapper for {name} missing class-level dunder {dunder}. "
                    f"This could break code that accesses {name}.{dunder}"
                )


# =============================================================================
# Regression Tests for Specific PRs
# =============================================================================


class TestPR15604Regression:
    """Regression tests for PR #15604: Enable sub-classing wrapped lock types.

    Before this fix, subclassing a wrapped lock type would fail with:
    TypeError: _LockAllocatorWrapper.__init__() takes 2 positional arguments but N were given

    The fix was to implement __mro_entries__ per PEP 560.
    """

    def test_neo4j_style_async_rlock(self) -> None:
        """Test the exact pattern that neo4j uses: class AsyncRLock(asyncio.Lock)."""
        init_ddup("test_neo4j_pattern")

        with AsyncioLockCollector(capture_pct=100):
            # This is the pattern that broke before PR #15604
            class AsyncRLock(asyncio.Lock):  # type: ignore[misc]
                """A reentrant lock for asyncio (neo4j-style)."""

                def __init__(self) -> None:
                    super().__init__()
                    self._owner: Optional[int] = None
                    self._count: int = 0

            # Should not raise
            lock = AsyncRLock()
            assert lock is not None

    def test_custom_semaphore_subclass(self) -> None:
        """Test subclassing threading.Semaphore with custom behavior."""
        init_ddup("test_custom_semaphore")

        with ThreadingSemaphoreCollector(capture_pct=100):

            class LimitedSemaphore(threading.Semaphore):  # type: ignore[misc]
                """Semaphore with additional tracking."""

                def __init__(self, value: int = 1) -> None:
                    super().__init__(value)
                    self.acquire_count: int = 0

                def acquire(self, *args: Any, **kwargs: Any) -> Any:
                    self.acquire_count += 1
                    return super().acquire(*args, **kwargs)

            sem = LimitedSemaphore(2)
            sem.acquire()
            assert sem.acquire_count == 1
            sem.release()


class TestPR15899Regression:
    """Regression tests for PR #15899: Enable serialization of wrapped lock objects.

    Before this fix, pickling a wrapped lock would fail or produce incorrect results.
    """

    def test_lock_survives_pickle_roundtrip(self) -> None:
        """Test that a lock can be pickled and unpickled successfully."""
        init_ddup("test_pickle_roundtrip")

        with ThreadingLockCollector(capture_pct=100):
            lock = threading.Lock()
            assert isinstance(lock, _ProfiledLock)

            # This should not raise
            pickled = pickle.dumps(lock)
            unpickled = pickle.loads(pickled)

            # Unpickled lock should be functional
            unpickled.acquire()
            unpickled.release()

    def test_lock_class_survives_pickle_roundtrip(self) -> None:
        """Test that the lock class (wrapper) can be pickled."""
        init_ddup("test_class_pickle")

        with ThreadingLockCollector(capture_pct=100):
            lock_class = threading.Lock
            assert isinstance(lock_class, _LockAllocatorWrapper)

            # This should not raise
            pickled = pickle.dumps(lock_class)
            unpickled = pickle.loads(pickled)

            # Unpickled class should be able to create locks
            lock = unpickled()
            lock.acquire()
            lock.release()

    def test_multiprocessing_scenario(self) -> None:
        """Test that locks work correctly in a multiprocessing-like scenario.

        This simulates what happens when passing locks to child processes.
        """
        init_ddup("test_multiprocessing")

        with ThreadingLockCollector(capture_pct=100):
            # Simulate the manager/worker pattern
            lock = threading.Lock()
            lock.acquire()

            # Pickle state of held lock (simulating passing to subprocess)
            pickled = pickle.dumps(lock)

            # Release in "parent"
            lock.release()

            # Unpickle in "child" - should get fresh unlocked lock
            child_lock = pickle.loads(pickled)
            assert child_lock.acquire(blocking=False) is True
            child_lock.release()
