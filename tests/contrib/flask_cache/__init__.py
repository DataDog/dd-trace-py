try:
    import flask_caching  # noqa: F401
except ImportError:
    # Legacy Flask-Cache only understands the short backend names.
    SIMPLE_CACHE = "simple"
    REDIS_CACHE = "redis"
    MEMCACHED_CACHE = "memcached"
else:
    # flask-caching 2.5 removed the short aliases ("simple", "redis", ...); the full class
    # paths work on every flask-caching version we test (>=1.10).
    SIMPLE_CACHE = "flask_caching.backends.SimpleCache"
    REDIS_CACHE = "flask_caching.backends.RedisCache"
    MEMCACHED_CACHE = "flask_caching.backends.MemcachedCache"
