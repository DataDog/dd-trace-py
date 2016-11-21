"""Instrument redis to report Redis queries.

Patch your redis client to make it work.

    from ddtrace import patch, Pin
    import redis

    # Patch redis
    patch(redis=True)


    # This will report a span with the default settings
    client = redis.StrictRedis(host="localhost", port=6379)
    client.get("my-key")

    # To customize one client
    Pin(service='my-redis').onto(client)
"""

from ..util import require_modules

required_modules = ['redis', 'redis.client']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        from .tracers import get_traced_redis, get_traced_redis_from

        __all__ = ['get_traced_redis', 'get_traced_redis_from', 'patch']
