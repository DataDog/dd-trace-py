"""
The MCP (Model Context Protocol) integration instruments the MCP Python library to emit traces for client tool calls
and server tool executions.

All traces submitted from the MCP integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.

Enabling
~~~~~~~~

The MCP integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.
Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the MCP integration::

    from ddtrace import patch
    patch(mcp=True)

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.mcp["service"]
   The service name reported by default for MCP requests.
   Alternatively, set this option with the ``DD_MCP_SERVICE`` environment variable.

.. py:data:: ddtrace.config.mcp["distributed_tracing"]
   Whether or not to enable distributed tracing for MCP requests.
   Alternatively, you can set this option with the ``DD_MCP_DISTRIBUTED_TRACING`` environment
   variable.
   Default: ``True``

.. py:data:: ddtrace.config.mcp["enrichment_enabled"]
   Whether to enrich MCP server tool calls for LLM Observability. Requires LLM Observability.
   Each tool input schema gets a ``telemetry`` argument, which the integration removes before the tool runs.
   The agent fills ``telemetry.intent`` with its reason for the call, which is recorded as the span's intent.
   ``telemetry.conversation_id`` groups tool calls into sessions when MCP has no session of its own, such as over
   stdio or with the stateless protocol. A tool call without it gets a generated ID, and its result gets an extra text
   block that asks the agent to send the ID on later calls. The ID is set as the ``session_id`` of the server tool span.
   Requests with an ``mcp-session-id`` header, and spans that already have a ``session_id``, keep their session.
   Tools that declare their own ``telemetry`` argument are not changed. The ``mcp_session_source`` tag shows whether
   the agent sent the ID back (``echoed``) or the integration generated it (``generated``).
   Set ``DD_MCP_CAPTURE_INTENT`` to turn intent capture on or off on its own.
   Alternatively, set this option with the ``DD_MCP_ENRICHMENT_ENABLED`` environment variable.
   Default: ``True``
"""  # noqa: E501
