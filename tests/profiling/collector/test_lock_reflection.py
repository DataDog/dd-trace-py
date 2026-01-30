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

AIDEV-NOTE: This is a meta-test module that uses reflection to catch wrapper
compatibility bugs automatically. Behavioral tests (acquire/release, pickle
roundtrips, subclassing) are in test_threading.py - this module focuses on
interface completeness via introspection.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Callable
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
# Protocol Definitions
# =============================================================================


@runtime_checkable
class ContextManagerProtocol(Protocol):
    """Context manager protocol for synchronous locks."""

    def __enter__(self) -> bool: ...

    def __exit__(
        self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException], exc_tb: object
    ) -> Optional[bool]: ...


@runtime_checkable
class AsyncContextManagerProtocol(Protocol):
    """Async context manager protocol for asyncio locks."""

    async def __aenter__(self) -> None: ...

    async def __aexit__(
        self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException], exc_tb: object
    ) -> None: ...


# =============================================================================
# Test Data - All lock types and their collectors
# =============================================================================

# Type aliases
LockConfig = Tuple[Type[object], Type[object], str]
CollectorConfig = Tuple[Type[object], str]

# Threading lock types with their collectors
THREADING_LOCK_CONFIGS: List[LockConfig] = [
    (threading.Lock, ThreadingLockCollector, "Lock"),
    (threading.RLock, ThreadingRLockCollector, "RLock"),
    (threading.Semaphore, ThreadingSemaphoreCollector, "Semaphore"),
    (threading.BoundedSemaphore, ThreadingBoundedSemaphoreCollector, "BoundedSemaphore"),
    (threading.Condition, ThreadingConditionCollector, "Condition"),
]

THREADING_COLLECTOR_CONFIGS: List[CollectorConfig] = [
    (collector, name) for _, collector, name in THREADING_LOCK_CONFIGS
]

# Asyncio lock types with their collectors
ASYNCIO_LOCK_CONFIGS: List[LockConfig] = [
    (asyncio.Lock, AsyncioLockCollector, "Lock"),
    (asyncio.Semaphore, AsyncioSemaphoreCollector, "Semaphore"),
    (asyncio.BoundedSemaphore, AsyncioBoundedSemaphoreCollector, "BoundedSemaphore"),
    (asyncio.Condition, AsyncioConditionCollector, "Condition"),
]

ASYNCIO_COLLECTOR_CONFIGS: List[CollectorConfig] = [(collector, name) for _, collector, name in ASYNCIO_LOCK_CONFIGS]

# Dunders to exclude from comparison between original and wrapped locks.
# Includes both universal object dunders and lock-specific exclusions.
EXCLUDED_DUNDERS: Set[str] = {
    # Universal object dunders (exist on all Python objects)
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
    # Lock-specific exclusions (intentionally not wrapped)
    "__class_getitem__",  # Generic type hints - not needed for locks
    "__weakref__",  # Weak references - optional
    "__dict__",  # We use __slots__
    "__module__",  # Inherited from object
}


# =============================================================================
# Helper Functions
# =============================================================================


def get_methods(obj: object, kind: str = "public") -> Set[str]:
    """Get callable methods from a class or instance.

    Args:
        obj: The object to inspect
        kind: "public" for non-underscore methods, "dunder" for __dunder__ methods

    Returns:
        Set of method names that are callable on the object.

    Note: This only finds methods visible in dir(). Methods delegated via
    __getattr__ won't appear here but are still accessible.
    """
    methods: Set[str] = set()
    for name in dir(obj):
        if kind == "public":
            if name.startswith("_"):
                continue
        elif kind == "dunder":
            if not (name.startswith("__") and name.endswith("__")):
                continue
            if name in EXCLUDED_DUNDERS:
                continue
        else:
            raise ValueError(f"kind must be 'public' or 'dunder', got {kind!r}")

        attr: object = getattr(obj, name, None)
        if callable(attr):
            methods.add(name)
    return methods


def check_method_accessible(obj: object, method_name: str) -> bool:
    """Check if a method is accessible on an object (even if via __getattr__)."""
    try:
        method: object = getattr(obj, method_name)
        return callable(method)
    except AttributeError:
        return False


# =============================================================================
# Reflection Tests - Interface Completeness
# =============================================================================


class TestWrapperInterfaceCompleteness:
    """Test that wrappers expose all expected dunders via reflection.

    These tests use introspection to automatically detect missing methods/dunders
    rather than testing specific behaviors (which are covered in test_threading.py).
    """

    @pytest.mark.parametrize("collector_class,name", THREADING_COLLECTOR_CONFIGS)
    def test_wrapper_exposes_critical_dunders(self, collector_class: Type[object], name: str) -> None:
        """Verify wrapper has critical dunders for common operations.

        Checks for __mro_entries__ (subclassing) and __reduce__ (pickling).
        """
        init_ddup(f"test_wrapper_dunders_{name}")

        with collector_class(capture_pct=100):  # type: ignore[attr-defined]
            wrapped: _LockAllocatorWrapper = getattr(threading, name)
            assert isinstance(wrapped, _LockAllocatorWrapper)

            # __mro_entries__ is needed for PEP 560 subclassing
            assert hasattr(wrapped, "__mro_entries__"), f"{name} wrapper missing __mro_entries__ (PEP 560)"

            # __reduce__ is needed for pickling
            assert hasattr(wrapped, "__reduce__"), f"{name} wrapper missing __reduce__ (pickling)"

    @pytest.mark.parametrize("lock_class,collector_class,name", THREADING_LOCK_CONFIGS)
    def test_profiled_lock_has_all_public_methods(
        self, lock_class: Type[object], collector_class: Type[object], name: str
    ) -> None:
        """Verify wrapped lock exposes all public methods of the original.

        Uses reflection to compare method sets between original and wrapped.
        """
        init_ddup(f"test_public_methods_{name}")

        # Get methods from unwrapped instance
        original_instance: object = lock_class()  # type: ignore[operator]
        original_methods: Set[str] = get_methods(original_instance)

        with collector_class(capture_pct=100):  # type: ignore[attr-defined]
            wrapped_class: object = getattr(threading, name)
            wrapped_instance: object = wrapped_class()  # type: ignore[operator]

            # All original methods should be ACCESSIBLE on wrapped instance
            inaccessible_methods: Set[str] = {
                method for method in original_methods if not check_method_accessible(wrapped_instance, method)
            }
            assert not inaccessible_methods, (
                f"Wrapped {name} has inaccessible methods: {inaccessible_methods}. "
                f"Original has: {sorted(original_methods)}"
            )

    @pytest.mark.parametrize("collector_class,name", THREADING_COLLECTOR_CONFIGS)
    def test_profiled_lock_context_manager_protocol(self, collector_class: Type[object], name: str) -> None:
        """Verify wrapped lock implements context manager protocol."""
        init_ddup(f"test_context_manager_{name}")

        with collector_class(capture_pct=100):  # type: ignore[attr-defined]
            wrapped_class: Callable[[], _ProfiledLock] = getattr(threading, name)
            lock: _ProfiledLock = wrapped_class()

            # Must have __enter__ and __exit__
            assert hasattr(lock, "__enter__"), f"{name} missing __enter__"
            assert hasattr(lock, "__exit__"), f"{name} missing __exit__"

            # Verify protocol compliance
            assert isinstance(lock, ContextManagerProtocol), f"{name} doesn't satisfy ContextManagerProtocol"


# =============================================================================
# Asyncio Reflection Tests
# =============================================================================


class TestAsyncioInterfaceCompleteness:
    """Reflection tests for asyncio lock wrappers.

    Note: These tests are synchronous because we're only checking for
    attribute presence via hasattr(), not actually running async code.
    """

    @pytest.mark.parametrize("collector_class,name", ASYNCIO_COLLECTOR_CONFIGS)
    def test_async_context_manager_protocol(self, collector_class: Type[object], name: str) -> None:
        """Verify wrapped asyncio locks implement async context manager protocol."""
        init_ddup(f"test_async_context_{name}")

        with collector_class(capture_pct=100):  # type: ignore[attr-defined]
            wrapped_class: Callable[[], _ProfiledLock] = getattr(asyncio, name)
            lock: _ProfiledLock = wrapped_class()

            # Must have __aenter__ and __aexit__
            assert hasattr(lock, "__aenter__"), f"asyncio.{name} missing __aenter__"
            assert hasattr(lock, "__aexit__"), f"asyncio.{name} missing __aexit__"


# =============================================================================
# Dunder Method Coverage - Core Reflection Tests
# =============================================================================


class TestDunderMethodCoverage:
    """Ensure all relevant dunder methods are implemented.

    This is the core meta-test that uses reflection to automatically detect
    missing dunders rather than relying solely on hardcoded lists.

    IMPORTANT: We check class.__dict__ (not hasattr/dir) to verify methods are
    EXPLICITLY defined, not just inherited from object. This catches bugs like
    missing __reduce__ which would otherwise inherit a broken default.
    """

    # Dunders that wrappers MUST explicitly override, and not inherit from `object`
    REQUIRED_WRAPPER_DUNDERS: Set[str] = {
        "__call__",  # To create instances
        "__get__",  # Prevent method binding
        "__mro_entries__",  # PEP 560 subclassing
        "__reduce__",  # Pickling
        "__init__",  # Initialization
        "__getattr__",  # Delegate to original
    }

    # Dunders that profiled locks MUST explicitly define
    REQUIRED_PROFILED_LOCK_DUNDERS: Set[str] = {
        "__enter__",  # Context manager
        "__exit__",  # Context manager
        "__eq__",  # Comparison
        "__hash__",  # For sets/dicts
        "__repr__",  # Debugging
        "__reduce__",  # Pickling - MUST override, object's default won't work
        "__getattr__",  # Delegate to wrapped
        "__init__",  # Initialization
    }

    # Dunders that profiled asyncio locks MUST additionally implement
    REQUIRED_ASYNC_DUNDERS: Set[str] = {
        "__aenter__",  # Async context manager
        "__aexit__",  # Async context manager
    }

    def test_lock_allocator_wrapper_has_required_dunders(self) -> None:
        """Verify _LockAllocatorWrapper explicitly defines all required dunder methods.

        Uses class.__dict__ to check for explicit definitions, not inherited methods.
        """
        # Check class.__dict__ to ensure methods are explicitly defined
        class_dict: Set[str] = set(_LockAllocatorWrapper.__dict__.keys())

        missing: Set[str] = self.REQUIRED_WRAPPER_DUNDERS - class_dict
        assert not missing, (
            f"_LockAllocatorWrapper missing explicitly defined dunders: {missing}. "
            f"These must be overriden by the class, not inherited from `object`."
        )

    def test_profiled_lock_has_required_dunders(self) -> None:
        """Verify _ProfiledLock explicitly defines all required dunder methods.

        Uses class.__dict__ to check for explicit definitions, not inherited methods.
        """
        # Check class.__dict__ to ensure methods are explicitly defined
        class_dict: Set[str] = set(_ProfiledLock.__dict__.keys())

        missing: Set[str] = self.REQUIRED_PROFILED_LOCK_DUNDERS - class_dict
        assert not missing, (
            f"_ProfiledLock missing explicitly defined dunders: {missing}. "
            f"These must be overriden by the class, not inherited from `object`."
        )

        # Also check async dunders
        missing_async: Set[str] = self.REQUIRED_ASYNC_DUNDERS - class_dict
        assert not missing_async, (
            f"_ProfiledLock missing explicitly defined async dunders: {missing_async}. "
            f"These must be overriden by the class, not inherited from `object`."
        )

    @pytest.mark.parametrize("lock_class,collector_class,name", THREADING_LOCK_CONFIGS)
    def test_wrapped_lock_has_original_dunders(
        self, lock_class: Type[object], collector_class: Type[object], name: str
    ) -> None:
        """Automatically detect missing dunders by comparing with original.

        Uses reflection to find dunders on the original lock type and
        verify they're accessible on the wrapped version.
        """
        init_ddup(f"test_dunders_{name}")

        # Get dunders from original
        original_instance: object = lock_class()  # type: ignore[operator]
        original_dunders: Set[str] = get_methods(original_instance, kind="dunder")

        with collector_class(capture_pct=100):  # type: ignore[attr-defined]
            wrapped_class: Callable[[], _ProfiledLock] = getattr(threading, name)
            wrapped_instance: _ProfiledLock = wrapped_class()
            wrapped_dunders: Set[str] = get_methods(wrapped_instance, kind="dunder")

            # Find dunders that are on original but missing from wrapped
            missing: Set[str] = original_dunders - wrapped_dunders

            assert not missing, (
                f"Wrapped {name} missing dunders from original: {missing}. If intentional, add to EXCLUDED_DUNDERS."
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
        self, lock_class: Type[object], collector_class: Type[object], name: str
    ) -> None:
        """Verify wrapper class exposes same dunders as original class.

        This checks the CLASS level (not instance), catching issues like
        missing __mro_entries__.
        """
        init_ddup(f"test_class_dunders_{name}")

        original_dunders: Set[str] = get_methods(lock_class, kind="dunder")

        with collector_class(capture_pct=100):  # type: ignore[attr-defined]
            wrapped_class: _LockAllocatorWrapper = getattr(threading, name)

            # The wrapper should expose dunders from the original class
            dunder: str
            for dunder in original_dunders:
                assert hasattr(wrapped_class, dunder), (
                    f"Wrapper for {name} missing class-level dunder {dunder}. "
                    f"This could break code that accesses {name}.{dunder}"
                )
