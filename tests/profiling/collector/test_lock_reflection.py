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

import threading
from typing import Callable
from typing import List
from typing import Set
from typing import Tuple
from typing import Type

import pytest

from ddtrace.profiling.collector._lock import _LockAllocatorWrapper
from ddtrace.profiling.collector._lock import _ProfiledLock
from ddtrace.profiling.collector.threading import ThreadingBoundedSemaphoreCollector
from ddtrace.profiling.collector.threading import ThreadingConditionCollector
from ddtrace.profiling.collector.threading import ThreadingLockCollector
from ddtrace.profiling.collector.threading import ThreadingRLockCollector
from ddtrace.profiling.collector.threading import ThreadingSemaphoreCollector
from tests.profiling.collector.test_utils import init_ddup


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

# Dunders to exclude from comparison - these are universal Python internals
# that we don't need to explicitly implement.
EXCLUDED_DUNDERS: Set[str] = {
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
    "__class_getitem__",  # Generic type hints - not needed for locks
    "__dict__",  # We use __slots__, so no __dict__
    "__module__",  # Inherited from object
    # Python 3.12+ class introspection attrs
    "__firstlineno__",
    "__static_attributes__",
}

# Dunders that the original has but we intentionally don't support (yet).
KNOWN_GAPS: Set[str] = {
    "__weakref__",  # TODO: Add to __slots__ to support weak references
}

# Dunders that MUST be explicitly defined in __dict__ (not inherited from object).
# Python looks up special methods on the class, and inherited defaults are broken.
# AIDEV-NOTE: These are critical - if removed, the wrapper breaks in subtle ways.
MUST_OVERRIDE_WRAPPER_DUNDERS: Set[str] = {
    "__call__",  # To create instances
    "__get__",  # Prevent method binding when used as class attribute
    "__mro_entries__",  # PEP 560 subclassing support
    "__reduce__",  # Pickling - object's default is broken for wrappers
    "__init__",  # Initialization
    "__getattr__",  # Delegate attribute access to original
}

MUST_OVERRIDE_LOCK_DUNDERS: Set[str] = {
    "__enter__",  # Context manager - must be on class for 'with' to work
    "__exit__",  # Context manager
    "__aenter__",  # Async context manager
    "__aexit__",  # Async context manager
    "__eq__",  # Comparison - special method lookup
    "__hash__",  # For sets/dicts - special method lookup
    "__repr__",  # String representation
    "__reduce__",  # Pickling - object's default is broken
    "__getattr__",  # Delegate attribute access to wrapped lock
    "__init__",  # Initialization
}


# =============================================================================
# Helper Functions
# =============================================================================


def get_methods(obj: object, kind: str = "public", callable_only: bool = True) -> Set[str]:
    """Get methods/attributes from a class or instance.

    Args:
        obj: The object to inspect
        kind: "public" for non-underscore methods, "dunder" for __dunder__ methods
        callable_only: If True, only return callable attributes. If False, include
                       non-callable attributes like __weakref__.

    Returns:
        Set of attribute names matching the criteria.

    Note: This only finds attributes visible in dir(). Methods delegated via
    __getattr__ won't appear here but are still accessible.
    """
    results: Set[str] = set()
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

        if callable_only:
            attr: object = getattr(obj, name, None)
            if not callable(attr):
                continue

        results.add(name)
    return results


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


# =============================================================================
# Class-Level Dunder Tests
# =============================================================================


class TestClassLevelDunders:
    """Test class-level dunder accessibility on wrapper classes.

    This checks that wrapper classes expose the same dunders as original classes
    when accessed at the class level (e.g., threading.Semaphore.__mro_entries__).
    """

    @pytest.mark.parametrize(
        "lock_class,collector_class,name",
        [
            # Only test pure Python classes that have meaningful class-level dunders
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


# =============================================================================
# Gap Discovery Tests - Find potentially missing features
# =============================================================================


class TestGapDiscovery:
    """Discover potential gaps in wrapper compatibility.

    These tests:
    1. Verify critical dunders are EXPLICITLY defined (in __dict__, not inherited)
    2. Compare ALL dunders between original and wrapped to find gaps

    AIDEV-NOTE: When these tests find a new gap, either:
    1. Add support for it in _lock.py, OR
    2. Add it to KNOWN_GAPS with a comment explaining why it's deferred
    """

    def test_wrapper_classes_override_critical_dunders(self) -> None:
        """Verify wrapper classes explicitly define critical dunders.

        Uses class.__dict__ to ensure methods are DEFINED, not inherited from object.
        This is critical because inherited defaults (like object.__reduce__) are broken.
        """
        # Check _LockAllocatorWrapper
        wrapper_dict: Set[str] = set(_LockAllocatorWrapper.__dict__.keys())
        missing_wrapper: Set[str] = MUST_OVERRIDE_WRAPPER_DUNDERS - wrapper_dict
        assert not missing_wrapper, (
            f"_LockAllocatorWrapper missing critical dunders: {missing_wrapper}. "
            f"These must be explicitly defined, not inherited from object."
        )

        # Check _ProfiledLock
        lock_dict: Set[str] = set(_ProfiledLock.__dict__.keys())
        missing_lock: Set[str] = MUST_OVERRIDE_LOCK_DUNDERS - lock_dict
        assert not missing_lock, (
            f"_ProfiledLock missing critical dunders: {missing_lock}. "
            f"These must be explicitly defined, not inherited from object."
        )

    @pytest.mark.parametrize("lock_class,collector_class,name", THREADING_LOCK_CONFIGS)
    def test_discover_missing_dunders(self, lock_class: Type[object], collector_class: Type[object], name: str) -> None:
        """Find dunders that original has but wrapped doesn't.

        This is a discovery test - it finds gaps we might not be aware of.
        Known gaps are tracked in KNOWN_GAPS and won't cause test failure.
        """
        init_ddup(f"test_discover_{name}")

        # Get ALL dunders from original (including non-callable like __weakref__)
        original_instance: object = lock_class()  # type: ignore[operator]
        original_dunders: Set[str] = get_methods(original_instance, kind="dunder", callable_only=False)

        with collector_class(capture_pct=100):  # type: ignore[attr-defined]
            wrapped_class: Callable[[], _ProfiledLock] = getattr(threading, name)
            wrapped_instance: _ProfiledLock = wrapped_class()
            wrapped_dunders: Set[str] = get_methods(wrapped_instance, kind="dunder", callable_only=False)

            # Find gaps: dunders on original but missing from wrapped
            all_missing: Set[str] = original_dunders - wrapped_dunders

            # Separate known gaps from unexpected gaps
            unexpected_gaps: Set[str] = all_missing - KNOWN_GAPS

            # Fail on unexpected gaps - these need to be addressed
            assert not unexpected_gaps, (
                f"Wrapped {name} missing unexpected dunders: {unexpected_gaps}. "
                f"Either add support in _lock.py, or add to KNOWN_GAPS if intentionally deferred."
            )

    def test_known_gaps_are_documented(self) -> None:
        """Verify KNOWN_GAPS doesn't grow too large.

        This is a meta-test to prevent accumulating too many deferred gaps.
        """
        assert len(KNOWN_GAPS) < 10, f"Too many known gaps ({len(KNOWN_GAPS)}). Consider fixing some: {KNOWN_GAPS}"
