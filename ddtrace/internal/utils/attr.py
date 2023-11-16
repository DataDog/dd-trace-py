import os
from typing import Callable  # noqa
from typing import Type  # noqa
from typing import TypeVar  # noqa
from typing import Union  # noqa


T = TypeVar("T")


def from_env(name, default, value_type):
    # type: (str, T, Union[Callable[[Union[str, T, None]], T], Type[T]]) -> Callable[[], T]
    def _():
        return value_type(os.environ.get(name, default))

    return _
