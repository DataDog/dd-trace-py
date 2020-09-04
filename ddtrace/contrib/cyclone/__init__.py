"""
The cyclone integration instruments the Cyclone web framework to trace web requests to and from Cyclone applications.


Enabling
~~~~~~~~

The integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`patch_all()<patch_all>`.

Or use :ref:`patch()<patch>` to manually enable the integration::

    from ddtrace import patch
    patch(cyclone=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.cyclone["service"]

   The service name reported by default for spans.

   This option can also be set with the ``DD_SERVICE`` environment
   variable.

   Default: ``"cyclone"``

.. py:data:: ddtrace.config.cyclone_client["service"]

   The service name reported by default for Cyclone http client instances.

   This option can also be set with the ``DD_CYCLONE_CLIENT_SERVICE`` environment variable.

   Default: ``"cyclone-client"``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the Cyclone integration on a per-client basis use the
``Pin`` API::

    import cyclone
    from ddtrace import Pin
"""
from ...utils.importlib import require_modules

required_modules = ["cyclone"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch, unpatch

        __all__ = ["patch", "unpatch"]
