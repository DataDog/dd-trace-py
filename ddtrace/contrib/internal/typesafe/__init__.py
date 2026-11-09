"""
The TypeSafe integration instruments the TypeSafe Python SDK (``typesafe-sdk``) to trace System One
decision requests made with ``TypeSafeClient.system_one`` and ``AsyncTypeSafeClient.system_one``.

All traces submitted from the TypeSafe integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.
- model used in the request.


Enabling
~~~~~~~~

The TypeSafe integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the TypeSafe integration::

    from ddtrace import config, patch

    patch(typesafe=True)

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.typesafe["service"]

   The service name reported by default for TypeSafe requests.

   Alternatively, you can set this option with the ``DD_TYPESAFE_SERVICE`` environment variable.
"""
