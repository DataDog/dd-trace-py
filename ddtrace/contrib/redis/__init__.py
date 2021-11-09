"""
The redis integration traces redis requests.


Enabling
~~~~~~~~

The redis integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`patch_all()<patch_all>`.

Or use :ref:`patch()<patch>` to manually enable the integration::

    from ddtrace import patch
    patch(redis=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.redis["service"]

   The service name reported by default for redis traces.

   This option can also be set with the ``DD_REDIS_SERVICE`` environment
   variable.

   Default: ``"redis"``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure particular redis instances use the :ref:`Pin<Pin>` API::

    import redis
    from ddtrace import Pin

    client = redis.StrictRedis(host="localhost", port=6379)

    # Override service name for this instance
    Pin.override(client, service="my-custom-queue")

    # Traces reported for this client will now have "my-custom-queue"
    # as the service name.
    client.get("my-key")
"""

from ...internal.utils.importlib import require_modules


required_modules = ["redis", "redis.client"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        from .tracers import get_traced_redis
        from .tracers import get_traced_redis_from

        __all__ = ["get_traced_redis", "get_traced_redis_from", "patch"]
