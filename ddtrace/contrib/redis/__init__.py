"""
Traces redis client queries.

If you are not autoinstrumenting with ``ddtrace-run`` then install the redis
instrumentation with::

    from ddtrace import patch
    patch(redis=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.redis["service"]

   The service name reported by default for your redis instances.

   Default: ``"redis"``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

    from ddtrace import Pin
    import redis

    # Override service name for this instance
    Pin.override(client, service="redis-queue")

    # This will report a span with the default settings
    client = redis.StrictRedis(host="localhost", port=6379)
    client.get("my-key")
"""

from ...utils.importlib import require_modules

required_modules = ["redis", "redis.client"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        from .tracers import get_traced_redis, get_traced_redis_from

        __all__ = ["get_traced_redis", "get_traced_redis_from", "patch"]
