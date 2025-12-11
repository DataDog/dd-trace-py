#!/usr/bin/env python

import asyncio
import sys
from typing import Any, Callable, Optional, Tuple, Type

print(f"Python: {sys.version}")


class _LockAllocatorWrapper:
    """Simulates _LockAllocatorWrapper from ddtrace to avoid dependency on ddtrace."""

    __slots__ = ("_func", "_original_class")

    def __init__(self, func: Callable[..., Any], original_class: Optional[Type[Any]] = None) -> None:
        self._func = func
        self._original_class = original_class

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._func(*args, **kwargs)

    def __mro_entries__(self, bases: Tuple[Any, ...]) -> Tuple[Type[Any], ...]:
        return (self._original_class,)  # type: ignore[return-value]


# Simulate profiler patching
_original_lock = asyncio.Lock
asyncio.Lock = _LockAllocatorWrapper(lambda: _original_lock(), original_class=_original_lock)  # type: ignore[assignment]

class AsyncRLock(asyncio.Lock):  # type: ignore[misc]
    def __init__(self) -> None:
        super().__init__()

# creates a custom lock that inherits from the wrapped lock - fails when `def __mro_entries__` is not implemented
lock = AsyncRLock()
print(f"SUCCESS: Created {lock}")
