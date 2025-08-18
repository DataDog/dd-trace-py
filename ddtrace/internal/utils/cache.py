from functools import wraps
from inspect import FullArgSpec
from inspect import getfullargspec
from inspect import isgeneratorfunction
from threading import RLock
from typing import Any
from typing import Callable
from typing import Optional
from typing import Type
from typing import TypeVar


miss = object()

T = TypeVar("T")
F = Callable[[T], Any]
M = Callable[[Any, T], Any]


class LFUCache(dict):
    """Simple LFU cache implementation.

    This cache is designed for memoizing functions with a single hashable
    argument. The eviction policy is LFU, i.e. the least frequently used values
    are evicted when the cache is full. The amortized cost of shrinking the
    cache when it grows beyond the requested size is O(log(size)).
    """

    def __init__(self, maxsize: int = 256) -> None:
        self.maxsize = maxsize
        self.lock = RLock()
        self.count_lock = RLock()

    def get(self, key: T, f: F) -> Any:  # type: ignore[override]
        """Get a value from the cache.

        If the value with the given key is not in the cache, the expensive
        function ``f`` is called on the key to generate it. The return value is
        then stored in the cache and returned to the caller.
        """

        _ = super(LFUCache, self).get(key, miss)
        if _ is not miss:
            with self.count_lock:
                value, count = _
                self[key] = (value, count + 1)
            return value

        with self.lock:
            _ = super(LFUCache, self).get(key, miss)
            if _ is not miss:
                with self.count_lock:
                    value, count = _
                    self[key] = (value, count + 1)
                return value

            # Cache miss: ensure that we have enough space in the cache
            # by evicting half of the entries when we go over the threshold
            while len(self) >= self.maxsize:
                for h in sorted(self, key=lambda h: self[h][1])[: self.maxsize >> 1]:
                    del self[h]

            value = f(key)

            self[key] = (value, 1)

            return value


def cached(maxsize: int = 256) -> Callable[[F], F]:
    """Decorator for memoizing functions of a single argument (LFU policy)."""

    def cached_wrapper(f: F) -> F:
        cache = LFUCache(maxsize)

        def cached_f(key: T) -> Any:
            return cache.get(key, f)

        cached_f.invalidate = cache.clear  # type: ignore[attr-defined]

        return cached_f

    return cached_wrapper


class CachedMethodDescriptor(object):
    def __init__(self, method: M, maxsize: int) -> None:
        self._method = method
        self._maxsize = maxsize

    def __get__(self, obj: Any, objtype: Optional[Type] = None) -> F:
        cached_method = cached(self._maxsize)(self._method.__get__(obj, objtype))
        setattr(obj, self._method.__name__, cached_method)
        return cached_method


def cachedmethod(maxsize: int = 256) -> Callable[[M], CachedMethodDescriptor]:
    """Decorator for memoizing methods of a single argument (LFU policy)."""

    def cached_wrapper(f: M) -> CachedMethodDescriptor:
        return CachedMethodDescriptor(f, maxsize)

    return cached_wrapper


def is_not_void_function(f: Callable, argspec: FullArgSpec) -> bool:
    return bool(
        argspec.args
        or argspec.varargs
        or argspec.varkw
        or argspec.defaults
        or argspec.kwonlyargs
        or argspec.kwonlydefaults
        or isgeneratorfunction(f)
    )


def callonce(f: Callable[[], Any]) -> Callable[[], Any]:
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
