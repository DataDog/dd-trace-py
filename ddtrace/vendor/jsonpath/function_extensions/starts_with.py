"""The `startswith` function extension."""

from ..function_extensions import ExpressionType
from ..function_extensions import FilterFunction


class StartsWith(FilterFunction):
    """The `startswith` function extension."""

    arg_types = [ExpressionType.VALUE, ExpressionType.VALUE]
    return_type = ExpressionType.LOGICAL

    def __call__(self, value: object, prefix: object) -> bool:
        """Return `True` if `value` starts with `prefix`."""
        if not isinstance(value, str) or not isinstance(prefix, str):
            return False

        try:
            return value.startswith(prefix)
        except AttributeError:
            return False
