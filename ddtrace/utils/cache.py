from threading import RLock
from typing import Any
from typing import Callable
from typing import Dict
from typing import Tuple
from typing import TypeVar


_CACHED_OBJECTS = []

miss = object()

T = TypeVar("T")
S = TypeVar("S")
F = Callable[[T], S]
M = Callable[[Any, T], S]


def cached(maxsize=256):
    # type: (int) -> Callable[[F], F]
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

                return result

        cached_f.invalidate = cache.clear  # type: ignore[attr-defined]
        _CACHED_OBJECTS.append(cached_f)

        return cached_f

    return cached_wrapper


class CachedMethod:
    def __init__(self, method, maxsize=256):
        # type: (F, int) -> None
        self._method = method
        self._cache = {}  # type: Dict[Any, Tuple[Any, int]]
        self._lock = RLock()
        self._maxsize = maxsize
        _CACHED_OBJECTS.append(self)

    def invalidate(self):
        # type: () -> None
        self._cache.clear()

    def __call__(self, key):
        # type: (T) -> S
        cache = self._cache
        maxsize = self._maxsize
        if len(self._cache) >= maxsize:
            for _, h in zip(range(maxsize >> 1), sorted(cache, key=lambda h: cache[h][1])):
                del cache[h]

        _ = cache.get(key, miss)
        if _ is not miss:
            value, count = _
            cache[key] = (value, count + 1)
            return value

        with self._lock:
            _ = cache.get(key, miss)
            if _ is not miss:
                value, count = _
                cache[key] = (value, count + 1)
                return value

            result = self._method(key)

            cache[key] = (result, 1)

            return result


def cachedmethod(maxsize=256):
    # type: (int) -> Callable[[M], M]
    def cached_wrapper(f):
        # type: (M) -> M
        def cached_f(self, key):
            # type: (Any, T) -> S
            method_name = f.__name__
            method = getattr(self, method_name)
            if not isinstance(method, CachedMethod):
                method = CachedMethod(f.__get__(self, type(self)), maxsize)  # type: ignore[attr-defined]
                setattr(self, method_name, method)
            return method(key)

        cached_f.invalidate = lambda: None  # type: ignore[attr-defined]

        return cached_f

    return cached_wrapper
