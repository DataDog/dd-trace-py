"""
The Anthropic integration instruments the Anthropic Python library to traces for requests made to the models for messages.

All traces submitted from the Anthropic integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.
- ``anthropic.request.model``: Anthropic model used in the request.
- ``anthropic.request.api_key``: Anthropic API key used to make the request (obfuscated to match the Anthropic UI representation ``sk-...XXXX`` where ``XXXX`` is the last 4 digits of the key).
- ``anthropic.request.parameters``: Parameters used in anthropic package call.


Enabling
~~~~~~~~

The Anthropic integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Note that these commands also enable the ``httpx`` integration which traces HTTP requests from the Anthropic library.

Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the Anthropic integration::

    from ddtrace import config, patch

    patch(anthropic=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.anthropic["service"]

   The service name reported by default for Anthropic requests.

   Alternatively, you can set this option with the ``DD_SERVICE`` or ``DD_ANTHROPIC_SERVICE`` environment
   variables.

   Default: ``DD_SERVICE``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the Anthropic integration on a per-instance basis use the
``Pin`` API::

    import anthropic
    from ddtrace import config
    from ddtrace._trace.pin import Pin

    Pin.override(anthropic, service="my-anthropic-service")
"""  # noqa: E501
