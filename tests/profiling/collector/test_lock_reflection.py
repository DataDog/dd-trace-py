"""Reflection-based tests for Lock Profiler wrapper interface completeness.

This module uses Python's introspection capabilities to verify that wrapped lock
types expose the same methods and dunders as their originals. It catches cases where
we forget to proxy a method that the original explicitly defines.

WHAT THESE TESTS CATCH:
- Missing public methods (acquire, release, locked, etc.)
- Missing dunders that originals explicitly define in __dict__ (__enter__, __exit__,
  __repr__, __init__, etc.)
- Interface drift if CPython changes lock APIs

WHAT THESE TESTS CANNOT CATCH:
- PR #15604 (__mro_entries__) and PR #15899 (__reduce__) - these involve methods
  that the ORIGINAL also inherits from object, so comparing __dict__ finds no gap.
  Such bugs require behavioral tests (actually pickle, actually subclass) which
  belong in test_threading.py.

AIDEV-NOTE: Pure reflection can only detect missing methods that the original
explicitly defines. It cannot detect when an inherited method (from object) is
semantically broken for our wrapper.
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
# AIDEV-NOTE: When fixing a gap, remove it from here and add proper support.
KNOWN_GAPS: Set[str] = {
    "__weakref__",  # TODO: Add to __slots__ to support weak references
}

# Dunders we intentionally don't mirror from originals (wrapper-specific behavior).
# These are dunders that originals might define but we handle differently.
WRAPPER_SPECIFIC_EXCLUSIONS: Set[str] = {
    "__new__",  # We use standard object.__new__
    "__slots__",  # Not a method
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


def get_explicitly_defined_dunders(cls_or_factory: object) -> Set[str]:
    """Get dunders that a class EXPLICITLY defines (not inherited).

    Uses class.__dict__ to find methods defined directly on the class,
    filtering out inherited methods from object or parent classes.

    For factory functions (like threading.Lock), creates an instance
    and introspects the resulting type instead.
    """
    # If it's a factory function (like threading.Lock), get the type it produces
    if callable(cls_or_factory) and not isinstance(cls_or_factory, type):
        try:
            instance: object = cls_or_factory()  # type: ignore[operator]
            target_class: type = type(instance)
        except Exception:
            return set()
    else:
        target_class = cls_or_factory  # type: ignore[assignment]

    # Get __dict__ from the class
    try:
        class_dict: Set[str] = set(target_class.__dict__.keys())
    except (TypeError, AttributeError):
        return set()

    return {
        name
        for name in class_dict
        if name.startswith("__")
        and name.endswith("__")
        and name not in EXCLUDED_DUNDERS
        and name not in WRAPPER_SPECIFIC_EXCLUSIONS
    }


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
    """Discover potential gaps in wrapper compatibility via dynamic reflection.

    These tests automatically detect missing dunders by comparing what originals
    EXPLICITLY define vs what our wrappers define. No hardcoded lists needed.

    AIDEV-NOTE: When these tests find a new gap, either:
    1. Add support for it in _lock.py, OR
    2. Add it to KNOWN_GAPS with a comment explaining why it's deferred
    """

    @pytest.mark.parametrize("lock_class,collector_class,name", THREADING_LOCK_CONFIGS)
    def test_wrapper_mirrors_original_explicit_dunders(
        self, lock_class: Type[object], collector_class: Type[object], name: str
    ) -> None:
        """Verify wrapper explicitly defines dunders that original explicitly defines.

        If the original lock class explicitly overrides a dunder (in its __dict__),
        our wrapper should too.

        NOTE: This would NOT have caught PR #15604 or #15899, since the originals
        also inherit __mro_entries__ and __reduce__ from object (not in their __dict__).
        Those bugs require behavioral tests.
        """
        init_ddup(f"test_mirror_dunders_{name}")

        # Get dunders that the ORIGINAL class explicitly defines
        original_explicit: Set[str] = get_explicitly_defined_dunders(lock_class)

        with collector_class(capture_pct=100):  # type: ignore[attr-defined]
            # Get dunders that _ProfiledLock explicitly defines
            wrapper_explicit: Set[str] = get_explicitly_defined_dunders(_ProfiledLock)

            # Find dunders that original defines but wrapper doesn't
            missing: Set[str] = original_explicit - wrapper_explicit - KNOWN_GAPS

            assert not missing, (
                f"Original {name} explicitly defines {missing}, but _ProfiledLock doesn't. "
                f"If original overrides a dunder, we probably should too. "
                f"Either add to _ProfiledLock, or add to KNOWN_GAPS if intentionally skipped."
            )

    @pytest.mark.parametrize("lock_class,collector_class,name", THREADING_LOCK_CONFIGS)
    def test_wrapped_instance_has_all_original_dunders(
        self, lock_class: Type[object], collector_class: Type[object], name: str
    ) -> None:
        """Find dunders that original instance has but wrapped instance doesn't.

        This catches non-callable dunders like __weakref__ that might be missing.
        """
        init_ddup(f"test_instance_dunders_{name}")

        # Get ALL dunders from original instance
        original_instance: object = lock_class()  # type: ignore[operator]
        original_dunders: Set[str] = get_methods(original_instance, kind="dunder", callable_only=False)

        with collector_class(capture_pct=100):  # type: ignore[attr-defined]
            wrapped_class: Callable[[], _ProfiledLock] = getattr(threading, name)
            wrapped_instance: _ProfiledLock = wrapped_class()
            wrapped_dunders: Set[str] = get_methods(wrapped_instance, kind="dunder", callable_only=False)

            # Find gaps
            missing: Set[str] = original_dunders - wrapped_dunders - KNOWN_GAPS

            assert not missing, (
                f"Wrapped {name} instance missing dunders: {missing}. "
                f"Either add support in _lock.py, or add to KNOWN_GAPS."
            )

    def test_known_gaps_are_documented(self) -> None:
        """Verify KNOWN_GAPS doesn't grow too large.

        This is a meta-test to prevent accumulating too many deferred gaps.
        """
        assert len(KNOWN_GAPS) < 10, f"Too many known gaps ({len(KNOWN_GAPS)}). Consider fixing some: {KNOWN_GAPS}"
