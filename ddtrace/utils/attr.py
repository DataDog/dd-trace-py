import os
from typing import Callable
from typing import Type
from typing import TypeVar


T = TypeVar("T")


def from_env(name, default, value_type):
    # type: (str, T, Type[T]) -> Callable[[], T]
    return lambda: value_type(os.environ.get(name, default))  # type: ignore
