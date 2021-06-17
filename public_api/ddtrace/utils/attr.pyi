from typing import Callable, Type, TypeVar, Union

T = TypeVar('T')

def from_env(name: str, default: T, value_type: Union[Callable[[Union[str, T, None]], T], Type[T]]) -> Callable[[], T]: ...
