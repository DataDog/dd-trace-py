from threading import RLock


def cached(maxsize=256):
    def cached_wrapper(f):
        cache = {}
        lock = RLock()
        miss = object()

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

        return cached_f

    return cached_wrapper


def cachedmethod(maxsize=256):
    def cached_wrapper(f):
        cache = {}
        lock = RLock()
        miss = object()

        def cached_f(self, key):
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

                result = f(self, key)

                cache[key] = (result, 1)

                return result

        return cached_f

    return cached_wrapper
