import os
from typing import Callable
from typing import Type
from typing import TypeVar
from typing import Union


T = TypeVar("T")


def from_env(name: str, default: T, value_type: Union[Callable[[Union[str, T, None]], T], Type[T]]) -> Callable[[], T]:
    def _():
        return value_type(os.environ.get(name, default))

    return _
