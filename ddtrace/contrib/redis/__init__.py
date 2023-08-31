"""
The redis integration traces redis requests.


Enabling
~~~~~~~~

The redis integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :func:`patch_all()<ddtrace.patch_all>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(redis=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.redis["service"]

   The service name reported by default for redis traces.

   This option can also be set with the ``DD_REDIS_SERVICE`` environment
   variable.

   Default: ``"redis"``


.. py:data:: ddtrace.config.redis["cmd_max_length"]

   Max allowable size for the redis command span tag.
   Anything beyond the max length will be replaced with ``"..."``.

   This option can also be set with the ``DD_REDIS_CMD_MAX_LENGTH`` environment
   variable.

   Default: ``1000``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure particular redis instances use the :class:`Pin <ddtrace.Pin>` API::

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
        from .patch import get_version
        from .patch import patch

        __all__ = ["patch", "get_version"]
