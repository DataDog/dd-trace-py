"""
The aredis integration traces aredis requests.


Enabling
~~~~~~~~

The aredis integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`patch_all()<patch_all>`.

Or use :ref:`patch()<patch>` to manually enable the integration::

    from ddtrace import patch
    patch(aredis=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.aredis["service"]

   The service name reported by default for aredis traces.

   This option can also be set with the ``DD_AREDIS_SERVICE`` environment
   variable.

   Default: ``"redis"``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure particular aredis instances use the :ref:`Pin<Pin>` API::

    import aredis
    from ddtrace import Pin

    client = aredis.StrictRedis(host="localhost", port=6379)

    # Override service name for this instance
    Pin.override(client, service="my-custom-queue")

    # Traces reported for this client will now have "my-custom-queue"
    # as the service name.
    async def example():
        await client.get("my-key")
"""

from ...internal.utils.importlib import require_modules


required_modules = ["aredis", "aredis.client"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch

        __all__ = ["patch"]
