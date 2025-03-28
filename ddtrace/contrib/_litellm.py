"""
The LiteLLM integration instruments the LiteLLM Python SDK's sync and async ``completion`` and ``text_completion`` methods.

All traces submitted from the LiteLLM integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.
- ``litellm.request.model``: Model used in the request. This may be just the model name (e.g. ``gpt-3.5-turbo``) or the model name with the route defined (e.g. ``openai/gpt-3.5-turbo``).
- ``litellm.request.host``: Host where the request is sent (if specified).


Enabling
~~~~~~~~

The LiteLLM integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the LiteLLM integration::

    from ddtrace import config, patch

    patch(litellm=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.litellm["service"]

   The service name reported by default for LiteLLM requests.

   Alternatively, you can set this option with the ``DD_SERVICE`` or ``DD_LITELLM_SERVICE`` environment
   variables.

   Default: ``DD_SERVICE``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the LiteLLM integration on a per-instance basis use the
``Pin`` API::

    import litellm
    from ddtrace import Pin, config

    Pin.override(litellm, service="my-litellm-service")
"""  # noqa: E501
