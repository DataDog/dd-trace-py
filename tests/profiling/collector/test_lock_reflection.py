"""Reflection-based tests for Lock Profiler wrapper interface completeness.

This module uses Python's introspection capabilities to verify that wrapped lock
types expose the same methods and dunders as their originals. It catches cases where
we forget to proxy a method that the original explicitly defines.

WHAT THESE TESTS CATCH:
- Missing public methods (acquire, release, locked, etc.)
- Missing dunders that originals define (__enter__, __exit__, __repr__, etc.)
- Interface drift if CPython changes lock APIs

WHAT THESE TESTS CANNOT CATCH:
- PR #15604 (__mro_entries__) and PR #15899 (__reduce__) - these involve methods
  that the ORIGINAL also inherits from object, so comparing finds no gap.
  Such bugs require behavioral tests (actually pickle, actually subclass) which
  are in test_threading.py.

Pure reflection can only detect missing methods that the original explicitly
defines. It cannot detect when an inherited method (from object) is semantically
broken for our wrapper.
"""

from __future__ import annotations

import threading
from typing import Callable
from typing import List
from typing import Set
from typing import Tuple
from typing import Type

import pytest

from ddtrace.profiling.collector._lock import _ProfiledLock
from ddtrace.profiling.collector.threading import ThreadingBoundedSemaphoreCollector
from ddtrace.profiling.collector.threading import ThreadingConditionCollector
from ddtrace.profiling.collector.threading import ThreadingLockCollector
from ddtrace.profiling.collector.threading import ThreadingRLockCollector
from ddtrace.profiling.collector.threading import ThreadingSemaphoreCollector
from tests.profiling.collector.test_utils import init_ddup


# =============================================================================
# Test Data
# =============================================================================

LockConfig = Tuple[Type[object], Type[object], str]

THREADING_LOCK_CONFIGS: List[LockConfig] = [
    (threading.Lock, ThreadingLockCollector, "Lock"),
    (threading.RLock, ThreadingRLockCollector, "RLock"),
    (threading.Semaphore, ThreadingSemaphoreCollector, "Semaphore"),
    (threading.BoundedSemaphore, ThreadingBoundedSemaphoreCollector, "BoundedSemaphore"),
    (threading.Condition, ThreadingConditionCollector, "Condition"),
]

# Dunders to exclude - universal Python internals we don't need to implement
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
    "__class_getitem__",
    "__dict__",
    "__module__",
    # Python 3.12+ class introspection attrs
    "__firstlineno__",
    "__static_attributes__",
}

# Dunders the original has but we intentionally don't support (yet).
# When fixing a gap, remove it from here and add proper support.
KNOWN_COVERAGE_GAPS: Set[str] = {
    "__weakref__",
}

MAX_ALLOWED_GAPS: int = 1


# =============================================================================
# Helpers
# =============================================================================


def get_public_methods(obj: object) -> Set[str]:
    """Get all public (non-underscore) callable methods from an object."""
    return {name for name in dir(obj) if not name.startswith("_") and callable(getattr(obj, name))}


def get_dunders(obj: object) -> Set[str]:
    """Get all dunder attributes from an object, excluding universal internals."""
    return {name for name in dir(obj) if name.startswith("__") and name.endswith("__") and name not in EXCLUDED_DUNDERS}


def is_accessible(obj: object, method_name: str) -> bool:
    """Check if a method is accessible on an object (even via __getattr__)."""
    try:
        return callable(getattr(obj, method_name))
    except AttributeError:
        return False


# =============================================================================
# Tests
# =============================================================================


@pytest.mark.parametrize("lock_class,collector_class,name", THREADING_LOCK_CONFIGS)
def test_public_methods_accessible(lock_class: Type[object], collector_class: Type[object], name: str) -> None:
    """Verify wrapped lock exposes all public methods of the original."""
    original: object = lock_class()  # type: ignore[operator]
    original_methods: Set[str] = get_public_methods(original)

    with collector_class(capture_pct=100):  # type: ignore[attr-defined]
        wrapped_class: Callable[[], _ProfiledLock] = getattr(threading, name)
        wrapped: _ProfiledLock = wrapped_class()
        missing: Set[str] = {m for m in original_methods if not is_accessible(wrapped, m)}

        assert not missing, (
            f"Wrapped {name} missing public methods: {missing}. Original has: {sorted(original_methods)}"
        )


@pytest.mark.parametrize("lock_class,collector_class,name", THREADING_LOCK_CONFIGS)
def test_dunders_accessible(lock_class: Type[object], collector_class: Type[object], name: str) -> None:
    """Verify wrapped lock exposes all dunders of the original."""
    init_ddup(f"test_dunders_{name}")

    original: object = lock_class()  # type: ignore[operator]
    original_dunders: Set[str] = get_dunders(original)

    with collector_class(capture_pct=100):  # type: ignore[attr-defined]
        wrapped_class: Callable[[], _ProfiledLock] = getattr(threading, name)
        wrapped: _ProfiledLock = wrapped_class()
        wrapped_dunders: Set[str] = get_dunders(wrapped)
        missing: Set[str] = original_dunders - wrapped_dunders - KNOWN_COVERAGE_GAPS

        assert not missing, (
            f"Wrapped {name} missing dunders: {missing}. Either add support in _lock.py, or add to KNOWN_COVERAGE_GAPS."
        )


def test_known_gaps_limit() -> None:
    """Meta-test: ensure KNOWN_COVERAGE_GAPS doesn't grow too large."""
    assert len(KNOWN_COVERAGE_GAPS) <= MAX_ALLOWED_GAPS, (
        f"Too many known gaps: {len(KNOWN_COVERAGE_GAPS)} (max is {MAX_ALLOWED_GAPS}). "
        f"Consider fixing some: {KNOWN_COVERAGE_GAPS}"
    )


def test_reflection_detects_missing_method() -> None:
    """Meta-test: verify reflection mechanism correctly detects missing methods.

    This test creates a deliberately incomplete wrapper to confirm that our
    reflection-based detection logic actually works. If this test passes,
    we can trust that test_public_methods_accessible and test_dunders_accessible
    would catch real missing methods.
    """

    class OriginalWithMethod:
        """Mock original class with a public method."""

        def some_method(self) -> str:
            return "original"

        def another_method(self) -> int:
            return 42

    class IncompleteWrapper:
        """Mock wrapper that intentionally omits some_method."""

        def another_method(self) -> int:
            return 42

    original = OriginalWithMethod()
    wrapper = IncompleteWrapper()

    original_methods: Set[str] = get_public_methods(original)
    assert "some_method" in original_methods, "Test setup: original should have some_method"
    assert "another_method" in original_methods, "Test setup: original should have another_method"

    # Verify is_accessible correctly identifies the missing method
    assert is_accessible(wrapper, "another_method"), "Wrapper has another_method"
    assert not is_accessible(wrapper, "some_method"), "Wrapper should NOT have some_method"

    # Verify the detection logic would catch this gap
    missing: Set[str] = {m for m in original_methods if not is_accessible(wrapper, m)}
    assert "some_method" in missing, f"Detection logic should find 'some_method' missing, but got: {missing}"
    assert "another_method" not in missing, (
        f"Detection logic should NOT report 'another_method' as missing, but got: {missing}"
    )
