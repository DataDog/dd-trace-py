"""A fluent API for working with `JSONPathMatch` iterators."""

from __future__ import annotations

import collections
import itertools
from enum import Enum
from enum import auto
from typing import TYPE_CHECKING
from typing import Any
from typing import Dict
from typing import Iterable
from typing import Iterator
from typing import List
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import Tuple
from typing import Union

if TYPE_CHECKING:
    from jsonpath import CompoundJSONPath
    from jsonpath import JSONPath
    from jsonpath import JSONPathEnvironment
    from jsonpath import JSONPathMatch
    from jsonpath import JSONPointer


class Projection(Enum):
    """Projection style used by `Query.select()`."""

    RELATIVE = auto()
    """The default projection. Selections include parent arrays and objects relative
    to the JSONPathMatch."""

    ROOT = auto()
    """Selections include parent arrays and objects relative to the root JSON value."""

    FLAT = auto()
    """All selections are appended to a new array/list, without arrays and objects
    on the path to the selected value."""


class Query:
    """A fluent API for managing `JSONPathMatch` iterators.

    Usually you'll want to use `jsonpath.query()` or `JSONPathEnvironment.query()`
    to create instances of `Query` rather than instantiating `Query` directly.

    Arguments:
        it: A `JSONPathMatch` iterable, as you'd get from `jsonpath.finditer()` or
            `JSONPathEnvironment.finditer()`.

    **New in version 1.1.0**
    """

    def __init__(self, it: Iterable[JSONPathMatch], env: JSONPathEnvironment) -> None:
        self._it = iter(it)
        self._env = env

    def __iter__(self) -> Iterator[JSONPathMatch]:
        return self._it

    def limit(self, n: int) -> Query:
        """Limit the query iterator to at most _n_ matches.

        Raises:
            ValueError: If _n_ < 0.
        """
        if n < 0:
            raise ValueError("can't limit by a negative number of matches")

        self._it = itertools.islice(self._it, n)
        return self

    def head(self, n: int) -> Query:
        """Limit the query iterator to at most the first _n_ matches.

        `head()` is an alias for `limit()`.

        Raises:
            ValueError: If _n_ < 0.
        """
        return self.limit(n)

    def first(self, n: int) -> Query:
        """Limit the query iterator to at most the first _n_ matches.

        `first()` is an alias for `limit()`.

        Raises:
            ValueError: If _n_ < 0.
        """
        return self.limit(n)

    def drop(self, n: int) -> Query:
        """Skip up to _n_ matches from the query iterator.

        Raises:
            ValueError: If _n_ < 0.
        """
        if n < 0:
            raise ValueError("can't drop a negative number of matches")

        if n > 0:
            next(itertools.islice(self._it, n, n), None)

        return self

    def skip(self, n: int) -> Query:
        """Skip up to _n_ matches from the query iterator.

        Raises:
            ValueError: If _n_ < 0.
        """
        return self.drop(n)

    def tail(self, n: int) -> Query:
        """Drop matches up to the last _n_ matches from the iterator.

        Raises:
            ValueError: If _n_ < 0.
        """
        if n < 0:
            raise ValueError("can't select a negative number of matches")

        self._it = iter(collections.deque(self._it, maxlen=n))
        return self

    def last(self, n: int) -> Query:
        """Drop up to the last _n_ matches from the iterator.

        `last()` is an alias for `tail()`.

        Raises:
            ValueError: If _n_ < 0.
        """
        return self.tail(n)

    def values(self) -> Iterable[object]:
        """Return an iterable of objects associated with each match."""
        return (m.obj for m in self._it)

    def locations(self) -> Iterable[str]:
        """Return an iterable of normalized paths, one for each match."""
        return (m.path for m in self._it)

    def items(self) -> Iterable[Tuple[str, object]]:
        """Return an iterable of (path, object) tuples, one for each match."""
        return ((m.path, m.obj) for m in self._it)

    def pointers(self) -> Iterable[JSONPointer]:
        """Return an iterable of JSONPointers, one for each match."""
        return (m.pointer() for m in self._it)

    def first_one(self) -> Optional[JSONPathMatch]:
        """Return the first `JSONPathMatch` or `None` if there were no matches."""
        try:
            return next(self._it)
        except StopIteration:
            return None

    def one(self) -> Optional[JSONPathMatch]:
        """Return the first `JSONPathMatch` or `None` if there were no matches.

        `one()` is an alias for `first_one()`.
        """
        return self.first_one()

    def last_one(self) -> Optional[JSONPathMatch]:
        """Return the last `JSONPathMatch` or `None` if there were no matches."""
        try:
            return next(iter(self.tail(1)))
        except StopIteration:
            return None

    def tee(self, n: int = 2) -> Tuple[Query, ...]:
        """Return _n_ independent queries by teeing this query's iterator.

        It is not safe to use a `Query` instance after calling `tee()`.
        """
        return tuple(Query(it, self._env) for it in itertools.tee(self._it, n))

    def take(self, n: int) -> Query:
        """Return a new query iterating over the next _n_ matches.

        It is safe to continue using this query after calling take.
        """
        return Query(list(itertools.islice(self._it, n)), self._env)

    def select(
        self,
        *expressions: Union[str, JSONPath, CompoundJSONPath],
        projection: Projection = Projection.RELATIVE,
    ) -> Iterable[object]:
        """Query projection using relative JSONPaths.

        Arguments:
            expressions: One or more JSONPath query expressions to select relative
                to each match in this query iterator.
            projection: The style of projection used when selecting values. Can be
                one of `Projection.RELATIVE`, `Projection.ROOT` or `Projection.FLAT`.
                Defaults to `Projection.RELATIVE`.

        Returns:
            An iterable of objects built from selecting _expressions_ relative to
                each match from the current query.

        **New in version 1.2.0**
        """
        return filter(
            bool,
            (self._select(m, expressions, projection) for m in self._it),
        )

    def _select(
        self,
        match: JSONPathMatch,
        expressions: Tuple[Union[str, JSONPath, CompoundJSONPath], ...],
        projection: Projection,
    ) -> object:
        if not isinstance(match.obj, (Mapping, Sequence)) or isinstance(match.obj, str):
            return None

        if projection == Projection.RELATIVE:
            obj: Dict[Union[int, str], Any] = {}
            for expr in expressions:
                path = self._env.compile(expr) if isinstance(expr, str) else expr
                for rel_match in path.finditer(match.obj):  # type: ignore
                    _patch_obj(rel_match.parts, obj, rel_match.obj)

            return _fix_sparse_arrays(obj)

        if projection == Projection.FLAT:
            arr: List[object] = []
            for expr in expressions:
                path = self._env.compile(expr) if isinstance(expr, str) else expr
                for rel_match in path.finditer(match.obj):  # type: ignore
                    arr.append(rel_match.obj)
            return arr

        # Project from the root document
        obj = {}
        for expr in expressions:
            path = self._env.compile(expr) if isinstance(expr, str) else expr
            for rel_match in path.finditer(match.obj):  # type: ignore
                _patch_obj(match.parts + rel_match.parts, obj, rel_match.obj)

        return _fix_sparse_arrays(obj)


def _patch_obj(
    parts: Tuple[Union[int, str], ...],
    obj: Mapping[Union[str, int], Any],
    value: object,
) -> None:
    _obj = obj

    # For lack of a better idea, we're patching arrays to dictionaries with
    # integer keys. This is to handle sparse array selections without having
    # to keep track of indexes and how they map from the root JSON value to
    # the selected JSON value.
    #
    # We'll fix these "sparse arrays" after the patch has been applied.
    for part in parts[:-1]:
        if part not in _obj:
            _obj[part] = {}  # type: ignore
        _obj = _obj[part]

    _obj[parts[-1]] = value  # type: ignore


def _fix_sparse_arrays(obj: Any) -> object:
    """Fix sparse arrays (dictionaries with integer keys)."""
    if isinstance(obj, str) or not obj:
        return obj

    if isinstance(obj, Sequence):
        return [_fix_sparse_arrays(e) for e in obj]

    if isinstance(obj, Mapping):
        if isinstance(next(iter(obj)), int):
            return [_fix_sparse_arrays(v) for v in obj.values()]
        return {k: _fix_sparse_arrays(v) for k, v in obj.items()}

    return obj
