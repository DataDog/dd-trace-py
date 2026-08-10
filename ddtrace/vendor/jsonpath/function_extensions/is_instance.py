"""A non-standard "isinstance" filter function."""

from typing import Mapping
from typing import Sequence

from ..filter import UNDEFINED
from ..filter import UNDEFINED_LITERAL
from ..function_extensions import ExpressionType
from ..function_extensions import FilterFunction
from ..match import NodeList


class IsInstance(FilterFunction):
    """A non-standard "isinstance" filter function."""

    arg_types = [ExpressionType.NODES, ExpressionType.VALUE]
    return_type = ExpressionType.LOGICAL

    def __call__(self, nodes: NodeList, t: str) -> bool:  # noqa: PLR0911
        """Return `True` if the type of _obj_ matches _t_.

        This function allows _t_ to be one of several aliases for the real
        Python "type". Some of these aliases follow JavaScript/JSON semantics.
        """
        if not nodes:
            return t in ("undefined", "missing")

        obj = nodes.values_or_singular()
        if (
            obj is UNDEFINED
            or obj is UNDEFINED_LITERAL
            or (isinstance(obj, NodeList) and len(obj) == 0)
        ):
            return t in ("undefined", "missing")

        if obj is None:
            return t in ("null", "nil", "None", "none")
        if isinstance(obj, str):
            return t in ("str", "string")
        if isinstance(obj, Sequence):
            return t in ("array", "list", "sequence", "tuple")
        if isinstance(obj, Mapping):
            return t in ("object", "dict", "mapping")
        if isinstance(obj, bool):
            return t in ("bool", "boolean")
        if isinstance(obj, int):
            return t in ("number", "int")
        if isinstance(obj, float):
            return t in ("number", "float")
        return t == "object"
