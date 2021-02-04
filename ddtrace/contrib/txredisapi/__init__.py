"""
The txredisapi integration instruments the redis client to submit traces for
redis requests.


Enabling
~~~~~~~~

The integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`patch_all()<patch_all>`.

Or use :ref:`patch()<patch>` to manually enable the integration::

    from ddtrace import patch
    patch(txredisapi=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.txredisapi["service"]

   The service name reported by default for txredis client instances.

   This option can also be set with the ``DD_TXREDISAPI_SERVICE`` environment
   variable.

   Default: ``"redis"``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the integration on an per-connection basis use the
``Pin`` API::

    import txredisapi
    from ddtrace import Pin, patch

    ddtrace.patch(txredisapi=True)

    rc = txredisapi.Connection()

    # Override the service name for this connection
    Pin.override(rc, service="redis-2")
"""


from ...utils.importlib import require_modules

required_modules = ["txredisapi"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch, unpatch

        __all__ = ["patch", "unpatch"]
