"""The JSONPath match object, as returned from `JSONPath.finditer()`."""

from __future__ import annotations

from typing import Any
from typing import List
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import Tuple
from typing import Union

from .pointer import JSONPointer
from .serialize import canonical_string

FilterContextVars = Mapping[str, Any]
PathPart = Union[int, str]


class JSONPathMatch:
    """A matched object with a concrete path.

    Attributes:
        children: Matched child nodes. This will only be populated after
            all children have been visited, usually by using `findall()`
            or `list(finditer())`.
        obj: The matched object.
        parent: The immediate parent to this match in the JSON document.
            If this is the root node, _parent_ will be `None`.
        path: The canonical string representation of the path to this match.
        parts: The keys, indices and/or slices that make up the path to this
            match.
        root: A reference to the root node in the JSON document.
    """

    __slots__ = (
        "_filter_context",
        "children",
        "obj",
        "parent",
        "parts",
        "path",
        "root",
    )

    pointer_class = JSONPointer

    def __init__(
        self,
        *,
        filter_context: FilterContextVars,
        obj: object,
        parent: Optional[JSONPathMatch],
        path: str,
        parts: Tuple[PathPart, ...],
        root: Union[Sequence[Any], Mapping[str, Any]],
    ) -> None:
        self._filter_context = filter_context
        self.children: List[JSONPathMatch] = []
        self.obj: object = obj
        self.parent: Optional[JSONPathMatch] = parent
        self.parts: Tuple[PathPart, ...] = parts
        self.path: str = path
        self.root: Union[Sequence[Any], Mapping[str, Any]] = root

    def __str__(self) -> str:
        return f"{_truncate(str(self.obj), 5)!r} @ {_truncate(self.path, 5)}"

    def add_child(self, *children: JSONPathMatch) -> None:
        """Append one or more children to this match."""
        self.children.extend(children)

    def new_child(self, obj: object, key: Union[int, str]) -> JSONPathMatch:
        """Return a new JSONPathMatch instance with this instance as its parent."""
        return self.__class__(
            filter_context=self.filter_context(),
            obj=obj,
            parent=self,
            parts=self.parts + (key,),
            path=self.path
            + f"[{canonical_string(key) if isinstance(key, str) else key}]",
            root=self.root,
        )

    def filter_context(self) -> FilterContextVars:
        """Return filter context data for this match."""
        return self._filter_context

    def pointer(self) -> JSONPointer:
        """Return a `JSONPointer` pointing to this match's path."""
        return JSONPointer.from_match(self)

    @property
    def value(self) -> object:
        """Return the value associated with this match/node."""
        return self.obj


def _truncate(val: str, num: int, end: str = "...") -> str:
    # Replaces consecutive whitespace with a single newline.
    # Treats quoted whitespace the same as unquoted whitespace.
    words = val.split()
    if len(words) < num:
        return " ".join(words)
    return " ".join(words[:num]) + end


class NodeList(List[JSONPathMatch]):
    """List of JSONPathMatch objects, analogous to the spec's nodelist."""

    def values(self) -> List[object]:
        """Return the values from this node list."""
        return [match.obj for match in self]

    def values_or_singular(self) -> object:
        """Return the values from this node list."""
        if len(self) == 1:
            return self[0].obj
        return [match.obj for match in self]

    def paths(self) -> List[str]:
        """Return a normalized path for each node in this node list."""
        return [match.path for match in self]

    def empty(self) -> bool:
        """Return `True` if this node list is empty."""
        return not bool(self)

    def __str__(self) -> str:
        return f"NodeList{super().__str__()}"
