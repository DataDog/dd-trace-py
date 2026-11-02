from importlib.metadata import PackageNotFoundError
from importlib.metadata import version


try:
    _modern_backends = int(version("Flask-Caching").split(".")[0]) >= 2
except PackageNotFoundError:
    _modern_backends = False

# Keep legacy alias coverage for Flask-Cache and Flask-Caching 1.x.
SIMPLE_CACHE = "SimpleCache" if _modern_backends else "simple"
REDIS_CACHE = "RedisCache" if _modern_backends else "redis"
MEMCACHED_CACHE = "MemcachedCache" if _modern_backends else "memcached"
