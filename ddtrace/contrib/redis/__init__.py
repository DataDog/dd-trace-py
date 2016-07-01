from ..util import require_modules

required_modules = ['redis', 'redis.client']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .tracers import get_traced_redis, get_traced_redis_from

        __all__ = ['get_traced_redis', 'get_traced_redis_from']
