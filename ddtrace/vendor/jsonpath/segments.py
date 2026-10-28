"""JSONPath child and descendant segment definitions."""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from typing import TYPE_CHECKING
from typing import AsyncIterable
from typing import Iterable
from typing import Mapping
from typing import Sequence
from typing import Tuple

from .exceptions import JSONPathRecursionError

if TYPE_CHECKING:
    from .env import JSONPathEnvironment
    from .match import JSONPathMatch
    from .selectors import JSONPathSelector
    from .token import Token


class JSONPathSegment(ABC):
    """Base class for all JSONPath segments."""

    __slots__ = ("env", "token", "selectors")

    def __init__(
        self,
        *,
        env: JSONPathEnvironment,
        token: Token,
        selectors: Tuple[JSONPathSelector, ...],
    ) -> None:
        self.env = env
        self.token = token
        self.selectors = selectors

    @abstractmethod
    def resolve(self, nodes: Iterable[JSONPathMatch]) -> Iterable[JSONPathMatch]:
        """Apply this segment to each `JSONPathMatch` in _nodes_."""

    @abstractmethod
    def resolve_async(
        self, nodes: AsyncIterable[JSONPathMatch]
    ) -> AsyncIterable[JSONPathMatch]:
        """An async version of `resolve`."""


class JSONPathChildSegment(JSONPathSegment):
    """The JSONPath child selection segment."""

    def resolve(self, nodes: Iterable[JSONPathMatch]) -> Iterable[JSONPathMatch]:
        """Select children of each node in _nodes_."""
        for node in nodes:
            for selector in self.selectors:
                yield from selector.resolve(node)

    async def resolve_async(
        self, nodes: AsyncIterable[JSONPathMatch]
    ) -> AsyncIterable[JSONPathMatch]:
        """An async version of `resolve`."""
        async for node in nodes:
            for selector in self.selectors:
                async for match in selector.resolve_async(node):
                    yield match

    def __str__(self) -> str:
        return f"[{', '.join(str(itm) for itm in self.selectors)}]"

    def __eq__(self, __value: object) -> bool:
        return (
            isinstance(__value, JSONPathChildSegment)
            and self.selectors == __value.selectors
            and self.token == __value.token
        )

    def __hash__(self) -> int:
        return hash((self.selectors, self.token))


class JSONPathRecursiveDescentSegment(JSONPathSegment):
    """The JSONPath recursive descent segment."""

    def resolve(self, nodes: Iterable[JSONPathMatch]) -> Iterable[JSONPathMatch]:
        """Select descendants of each node in _nodes_."""
        for node in nodes:
            for _node in self._visit(node):
                for selector in self.selectors:
                    yield from selector.resolve(_node)

    async def resolve_async(
        self, nodes: AsyncIterable[JSONPathMatch]
    ) -> AsyncIterable[JSONPathMatch]:
        """An async version of `resolve`."""
        async for node in nodes:
            for _node in self._visit(node):
                for selector in self.selectors:
                    async for match in selector.resolve_async(_node):
                        yield match

    def _visit(self, node: JSONPathMatch, depth: int = 1) -> Iterable[JSONPathMatch]:
        """Depth-first, pre-order node traversal."""
        if depth > self.env.max_recursion_depth:
            raise JSONPathRecursionError("recursion limit exceeded", token=self.token)

        yield node

        if isinstance(node.obj, Mapping):
            for name, val in node.obj.items():
                if isinstance(val, (Mapping, Sequence)):
                    _node = node.new_child(val, name)
                    yield from self._visit(_node, depth + 1)
        elif isinstance(node.obj, Sequence) and not isinstance(node.obj, str):
            for i, item in enumerate(node.obj):
                if isinstance(item, (Mapping, Sequence)):
                    _node = node.new_child(item, i)
                    yield from self._visit(_node, depth + 1)

    def __str__(self) -> str:
        return f"..[{', '.join(str(itm) for itm in self.selectors)}]"

    def __eq__(self, __value: object) -> bool:
        return (
            isinstance(__value, JSONPathRecursiveDescentSegment)
            and self.selectors == __value.selectors
            and self.token == __value.token
        )

    def __hash__(self) -> int:
        return hash(("..", self.selectors, self.token))
