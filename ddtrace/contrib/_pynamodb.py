"""
The PynamoDB integration traces all db calls made with the pynamodb
library through the connection API.

Enabling
~~~~~~~~

The PynamoDB integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    import pynamodb
    from ddtrace import patch, config
    patch(pynamodb=True)

Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.pynamodb["service"]

   The service name reported by default for the PynamoDB instance.

   This option can also be set with the ``DD_PYNAMODB_SERVICE`` environment
   variable.

   Default: ``"pynamodb"``

"""
