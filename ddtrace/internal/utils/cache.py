from functools import lru_cache
from functools import wraps
from inspect import FullArgSpec
from inspect import getfullargspec
from inspect import isgeneratorfunction
from typing import Any  # noqa:F401
from typing import Callable  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Type  # noqa:F401
from typing import TypeVar


miss = object()

T = TypeVar("T")
F = Callable[[T], Any]
M = Callable[[Any, T], Any]


def cached(maxsize: int = 256) -> Callable[[Callable], Callable]:
    def _(f: Callable) -> Callable:
        return lru_cache(maxsize)(f)

    return _


class CachedMethodDescriptor:
    def __init__(self, method, maxsize):
        # type: (M, int) -> None
        self._method = method
        self._maxsize = maxsize

    def __get__(self, obj, objtype=None):
        # type: (Any, Optional[Type]) -> F
        cached_method = cached(self._maxsize)(self._method.__get__(obj, objtype))
        setattr(obj, self._method.__name__, cached_method)
        return cached_method


def cachedmethod(maxsize=256):
    # type: (int) -> Callable[[M], CachedMethodDescriptor]
    """Decorator for memoizing methods of a single argument (LFU policy)."""

    def cached_wrapper(f):
        # type: (M) -> CachedMethodDescriptor
        return CachedMethodDescriptor(f, maxsize)

    return cached_wrapper


def is_not_void_function(f, argspec: FullArgSpec):
    return (
        argspec.args
        or argspec.varargs
        or argspec.varkw
        or argspec.defaults
        or argspec.kwonlyargs
        or argspec.kwonlydefaults
        or isgeneratorfunction(f)
    )


def callonce(f):
    # type: (Callable[[], Any]) -> Callable[[], Any]
    """Decorator for executing a function only the first time."""
    argspec = getfullargspec(f)
    if is_not_void_function(f, argspec):
        raise ValueError("The callonce decorator can only be applied to functions with no arguments")

    @wraps(f)
    def _():
        # type: () -> Any
        try:
            retval, exc = f.__callonce_result__  # type: ignore[attr-defined]
        except AttributeError:
            try:
                retval = f()
                exc = None
            except Exception as e:
                retval = None
                exc = e
            f.__callonce_result__ = retval, exc  # type: ignore[attr-defined]

        if exc is not None:
            raise exc

        return retval

    return _
