"""Shared test assertions for lock profiler tests.

Contains reusable test logic that applies to both threading and asyncio lock collectors.
For test data helpers (line numbers, etc.), see lock_utils.py.
"""

from __future__ import annotations

from types import UnionType

from ddtrace.profiling.collector._lock import _LockAllocatorWrapper


def assert_pep604_type_union_syntax(lock_class: _LockAllocatorWrapper) -> None:
    """Assert that PEP 604 type union syntax works with a wrapped lock class.

    Reproduces https://github.com/DataDog/dd-trace-py/issues/16375 where
    `asyncio.Condition | None` raised TypeError because _LockAllocatorWrapper
    didn't support the `|` operator used for type unions.
    """
    assert isinstance(lock_class, _LockAllocatorWrapper)

    union: UnionType = lock_class | None  # type: ignore[operator]
    assert isinstance(union, UnionType)

    union_rev: UnionType = None | lock_class  # type: ignore[operator]
    assert isinstance(union_rev, UnionType)
