from typing import Any, Callable, Optional, Type, TypeVar

miss: Any
T = TypeVar('T')
S = TypeVar('S')
F = Callable[[T], S]
M = Callable[[Any, T], S]

def cached(maxsize: int=...) -> Callable[[F], F]: ...

class CachedMethodDescriptor:
    def __init__(self, method: M, maxsize: int) -> None: ...
    def __get__(self, obj: Any, objtype: Optional[Type]=...) -> F: ...

def cachedmethod(maxsize: int=...) -> Callable[[M], CachedMethodDescriptor]: ...
