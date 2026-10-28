"""Classes modeling the JSONPath spec type system for function extensions."""
from abc import ABC
from abc import abstractmethod
from enum import Enum
from typing import Any
from typing import List


class ExpressionType(Enum):
    """The type of a filter function argument or return value."""

    VALUE = 1
    LOGICAL = 2
    NODES = 3


class FilterFunction(ABC):
    """Base class for typed function extensions."""

    @property
    @abstractmethod
    def arg_types(self) -> List[ExpressionType]:
        """Argument types expected by the filter function."""

    @property
    @abstractmethod
    def return_type(self) -> ExpressionType:
        """The type of the value returned by the filter function."""

    @abstractmethod
    def __call__(self, *args: Any, **kwds: Any) -> Any:
        """Called the filter function."""
