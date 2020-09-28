"""
The twisted integration provides context propagation for tracing
across Twisted Deferreds.


Enabling
~~~~~~~~

The integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`patch_all()<patch_all>`.

Or use :ref:`patch()<patch>` to manually enable the integration::

    from ddtrace import patch
    patch(twisted=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.twisted["client-service"]

   The service name reported by default for http client requests.

   This option can also be set with the ``DD_TWISTED_CLIENT_SERVICE`` environment variable.

   Default: ``"twisted-request"``

.. py:data:: ddtrace.config.twisted["split_by_domain"]

   Use the domain name of the request as the service for spans.

   This option can also be set with the ``DD_TWISTED_SPLIT_BY_DOMAIN`` environment variable.

   Default: ``"grpc-server"``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the integration on an per-connection basis use the
``Pin`` API::

"""

from ...utils.importlib import require_modules


required_modules = ["twisted"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch, unpatch

        __all__ = ["patch", "unpatch"]
