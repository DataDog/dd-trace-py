from __future__ import annotations

from collections.abc import Iterator
from collections.abc import Mapping
from typing import Generic
from typing import TypeVar


_T = TypeVar("_T")


class MultiDict(Mapping[str, _T], Generic[_T]):
    """Small subset of multidict used by the vendored grpclib client."""

    def __init__(self, values=()):
        self._items = list(values.items() if isinstance(values, Mapping) else values)

    def add(self, key: str, value: _T) -> None:
        self._items.append((key, value))

    def __getitem__(self, key: str) -> _T:
        for item_key, value in reversed(self._items):
            if item_key == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(dict(self._items))

    def __len__(self) -> int:
        return len(dict(self._items))

    def items(self):
        return tuple(self._items)
