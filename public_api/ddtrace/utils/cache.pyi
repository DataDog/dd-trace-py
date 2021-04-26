import Any
from typing import Callable, TypeVar

miss: Any
T = TypeVar('T')
S = TypeVar('S')
F = Callable[[T], S]
M = Callable[[Any, T], S]

def cached(maxsize: builtins.int=...) -> Callable[[F], F]: ...

class CachedMethodDescriptor:
    def __init__(self, method: def (Any, Any) -> Any, maxsize: builtins.int) -> None: ...
    def __get__(self, obj: Any, objtype: Union[Type[Any], None]=...) -> F: ...

def cachedmethod(maxsize: builtins.int=...) -> Callable[[M], CachedMethodDescriptor]: ...
