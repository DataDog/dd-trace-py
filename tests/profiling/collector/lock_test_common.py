"""Shared test assertions for lock profiler tests.

Contains reusable test logic that applies to both threading and asyncio lock collectors.
For test data helpers (line numbers, etc.), see lock_utils.py.
"""

from __future__ import annotations

import types
from typing import TYPE_CHECKING
from typing import Any
from typing import Optional

import pytest

from ddtrace.profiling.collector._lock import _LockAllocatorWrapper


if TYPE_CHECKING:
    from types import UnionType


def assert_pep604_type_union_syntax(lock_class: _LockAllocatorWrapper) -> None:
    """Assert that PEP 604 type union syntax works with a wrapped lock class.

    Reproduces https://github.com/DataDog/dd-trace-py/issues/16375 where
    `asyncio.Condition | None` raised TypeError because _LockAllocatorWrapper
    didn't support the `|` operator used for type unions.

    Requires Python 3.10+ (PEP 604). Callers must skip on older versions.
    """
    original: Optional[type[Any]] = lock_class._original_class
    if not isinstance(original, type):
        pytest.skip("Original lock is a factory function, not a type — PEP 604 union not supported natively")

    assert isinstance(lock_class, _LockAllocatorWrapper)

    union: UnionType = lock_class | None
    assert isinstance(union, types.UnionType)
    assert union.__args__ == (original, type(None))

    runion: UnionType = None | lock_class
    assert isinstance(runion, types.UnionType)
    assert runion.__args__ == (type(None), original)
