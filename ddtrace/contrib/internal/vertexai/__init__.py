"""
The Vertex AI integration instruments the Vertex Generative AI SDK for Python for requests made to Google models.

All traces submitted from the Vertex AI integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.
- ``vertexai.request.provider``: LLM provider used in the request (e.g. ``google`` for Google models).
- ``vertexai.request.model``: Google model used in the request.


Enabling
~~~~~~~~

The Vertex AI integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the Vertex AI integration::

    from ddtrace import config, patch

    patch(vertexai=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.vertexai["service"]

   The service name reported by default for Vertex AI requests.

   Alternatively, you can set this option with the ``DD_SERVICE`` or ``DD_VERTEXAI_SERVICE`` environment
   variables.

   Default: ``DD_SERVICE``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the Vertex AI integration on a per-instance basis use the
``Pin`` API::

    import vertexai
    from ddtrace import config
    from ddtrace._trace.pin import Pin

    Pin.override(vertexai, service="my-vertexai-service")
"""  # noqa: E501
