"""The standard `count` function extension."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..function_extensions import ExpressionType
from ..function_extensions import FilterFunction

if TYPE_CHECKING:
    from jsonpath.match import NodeList


class Count(FilterFunction):
    """The built-in `count` function."""

    arg_types = [ExpressionType.NODES]
    return_type = ExpressionType.VALUE

    def __call__(self, node_list: NodeList) -> int:
        """Return the number of nodes in the node list."""
        return len(node_list)
