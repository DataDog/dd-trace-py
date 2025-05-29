"""
The Mistral integration instruments the mistralai Python library to emit traces for agent runs.
All traces submitted from the Mistral integration are tagged by:
- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.
Enabling
~~~~~~~~
The Mistral integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.
Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the Mistral integration::
    from ddtrace import patch
    patch(mistralai=True)
Global Configuration
~~~~~~~~~~~~~~~~~~~~
.. py:data:: ddtrace.config.mistralai["service"]
   The service name reported by default for Mistral requests.
   Alternatively, you can set this option with the ``DD_SERVICE`` or ``DD_MISTRAL_SERVICE`` environment
   variables.
   Default: ``DD_SERVICE``
Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~
To configure the Mistral integration on a per-instance basis use the
``Pin`` API::
    import mistralai
    from ddtrace.trace import Pin
    Pin.override(mistralai, service="my-mistral-service")
"""  # noqa: E501
