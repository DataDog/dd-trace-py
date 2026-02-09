"""
The PydanticAI integration instruments the PydanticAI agent framework library.

All traces submitted from the PydanticAI integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.


Enabling
~~~~~~~~

The PydanticAI integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the PydanticAI integration::

    from ddtrace import patch

    patch(pydantic_ai=True)


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.pydantic_ai["service"]

   The service name reported by default for PydanticAI requests.

   Alternatively, set this option with the ``DD_PYDANTIC_AI_SERVICE`` environment variable.
"""  # noqa: E501
