from threading import RLock


_CACHED_OBJECTS = []

miss = object()


def cached(maxsize=256):
    def cached_wrapper(f):
        cache = {}
        lock = RLock()

        def cached_f(key):
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

        cached_f.invalidate = cache.clear
        _CACHED_OBJECTS.append(cached_f)

        return cached_f

    return cached_wrapper


class CachedMethod:
    def __init__(self, method, maxsize=256):
        self._method = method
        self._cache = {}
        self._lock = RLock()
        self._maxsize = maxsize
        _CACHED_OBJECTS.append(self)

    def invalidate(self):
        self._cache.clear()

    def __call__(self, key):
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
    def cached_wrapper(f):
        def cached_f(self, key):
            method_name = f.__name__
            method = getattr(self, method_name)
            if not isinstance(method, CachedMethod):
                method = CachedMethod(f.__get__(self, type(self)), maxsize)
                setattr(self, method_name, method)
            return method(key)

        cached_f.invalidate = lambda: None

        return cached_f

    return cached_wrapper
