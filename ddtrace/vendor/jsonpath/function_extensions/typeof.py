"""A non-standard "typeof" filter function."""

from typing import Mapping
from typing import Sequence

from ..filter import UNDEFINED
from ..filter import UNDEFINED_LITERAL
from ..function_extensions import ExpressionType
from ..function_extensions import FilterFunction
from ..match import NodeList


class TypeOf(FilterFunction):
    """A non-standard "typeof" filter function.

    Arguments:
        single_number_type: If True, will return "number" for ints and floats,
            otherwise we'll use "int" and "float" respectively. Defaults to `True`.
    """

    arg_types = [ExpressionType.NODES]
    return_type = ExpressionType.VALUE

    def __init__(self, *, single_number_type: bool = True) -> None:
        self.single_number_type = single_number_type

    def __call__(self, nodes: NodeList) -> str:  # noqa: PLR0911
        """Return the type of _obj_ as a string.

        The strings returned from this function use JSON terminology, much
        like the result of JavaScript's `typeof` operator.
        """
        if not nodes:
            return "undefined"

        obj = nodes.values_or_singular()

        if obj is UNDEFINED or obj is UNDEFINED_LITERAL:
            return "undefined"
        if obj is None:
            return "null"
        if isinstance(obj, str):
            return "string"
        if isinstance(obj, Sequence):
            return "array"
        if isinstance(obj, Mapping):
            return "object"
        if isinstance(obj, bool):
            return "boolean"
        if isinstance(obj, int):
            return "number" if self.single_number_type else "int"
        if isinstance(obj, float):
            return "number" if self.single_number_type else "float"
        return "object"
