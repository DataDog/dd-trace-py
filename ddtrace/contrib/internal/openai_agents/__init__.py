"""
The OpenAI Agents integration instruments the openai-agents Python library to emit traces for agent workflows.

All traces submitted from the OpenAI Agents integration are tagged by:
- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.

Enabling
~~~~~~~~

The OpenAI Agents integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the OpenAI Agents integration::

    from ddtrace import patch

    patch(openai_agents=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.openai_agents["service"]

   The service name reported by default for OpenAI Agents requests.

   Alternatively, you can set this option with the ``DD_SERVICE`` or ``DD_OPENAI_AGENTS_SERVICE`` environment
   variables.

   Default: ``DD_SERVICE``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the OpenAI Agents integration on a per-instance basis use the
``Pin`` API::

    import agents
    from ddtrace.trace import Pin

    Pin.override(agents, service="my-agents-service")
"""  # noqa: E501
