"""The `keys` JSONPath filter function."""

from typing import Mapping
from typing import Tuple
from typing import Union

from ..filter import UNDEFINED
from ..filter import _Undefined

from .filter_function import ExpressionType
from .filter_function import FilterFunction


class Keys(FilterFunction):
    """The `keys` JSONPath filter function."""

    arg_types = [ExpressionType.VALUE]
    return_type = ExpressionType.VALUE

    def __call__(
        self, value: Mapping[str, object]
    ) -> Union[Tuple[str, ...], _Undefined]:
        """Return a tuple of keys in `value`.

        If `value` does not have a `keys()` method, the special _Nothing_ value
        is returned.
        """
        try:
            return tuple(value.keys())
        except AttributeError:
            return UNDEFINED
