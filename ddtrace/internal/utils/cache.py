from threading import RLock
from typing import Any
from typing import Callable
from typing import Dict
from typing import Optional
from typing import Tuple
from typing import Type
from typing import TypeVar


miss = object()

T = TypeVar("T")
F = Callable[[T], Any]
M = Callable[[Any, T], Any]


def cached(maxsize=256):
    # type: (int) -> Callable[[F], F]
    """
    Decorator for caching the result of functions with a single argument.

    The strategy is MFU, meaning that only the most frequently used values are
    retained. The amortized cost of shrinking the cache when it grows beyond
    the requested size is O(log(size)).
    """

<<<<<<< HEAD
    def cached_wrapper(f):
        # type: (F) -> F
        cache = {}  # type: Dict[Any, Tuple[Any, int]]
        lock = RLock()

        def cached_f(key):
            # type: (T) -> S
            if len(cache) >= maxsize:
                for _, h in zip(range(maxsize >> 1), sorted(cache, key=lambda h: cache[h][1])):
                    del cache[h]

            _ = cache.get(key, miss)
=======
    def __init__(self, maxsize=256):
        # type: (int) -> None
        self.maxsize = maxsize
        self.lock = RLock()

    def get(self, key, f):  # type: ignore[override]
        # type: (T, F) -> Any
        """Get a value from the cache.

        If the value with the given key is not in the cache, the expensive
        function ``f`` is called on the key to generate it. The return value is
        then stored in the cache and returned to the caller.
        """
        if len(self) >= self.maxsize:
            for _, h in zip(range(self.maxsize >> 1), sorted(self, key=lambda h: self[h][1])):
                del self[h]

        _ = super(LFUCache, self).get(key, miss)
        if _ is not miss:
            value, count = _
            self[key] = (value, count + 1)
            return value

        with self.lock:
            _ = super(LFUCache, self).get(key, miss)
>>>>>>> 026d6144 (fix(typing): update types to be compatible with latest mypy (backport #4234) (#4263))
            if _ is not miss:
                value, count = _
                cache[key] = (value, count + 1)
                return value

            with lock:
                _ = cache.get(key, miss)
                if _ is not miss:
                    value, count = _
                    cache[key] = (value, count + 1)
                    return value

                result = f(key)

                cache[key] = (result, 1)

<<<<<<< HEAD
                return result
=======

def cached(maxsize=256):
    # type: (int) -> Callable[[F], F]
    """Decorator for memoizing functions of a single argument (LFU policy)."""

    def cached_wrapper(f):
        # type: (F) -> F
        cache = LFUCache(maxsize)

        def cached_f(key):
            # type: (T) -> Any
            return cache.get(key, f)
>>>>>>> 026d6144 (fix(typing): update types to be compatible with latest mypy (backport #4234) (#4263))

        cached_f.invalidate = cache.clear  # type: ignore[attr-defined]

        return cached_f

    return cached_wrapper


class CachedMethodDescriptor(object):
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
    def cached_wrapper(f):
        # type: (M) -> CachedMethodDescriptor
        return CachedMethodDescriptor(f, maxsize)

    return cached_wrapper
