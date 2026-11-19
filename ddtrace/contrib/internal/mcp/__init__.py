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

.. py:data:: ddtrace.config.mcp["capture_session"]
   Whether to group MCP server tool calls into LLM Observability sessions when MCP has no session of its own,
   such as over stdio or with the stateless protocol. Requires LLM Observability.
   When enabled, each tool input schema gets an optional ``telemetry.conversation_id`` argument. A tool call
   without it gets a generated ID, and its result gets an extra text block that asks the agent to send the ID
   on later calls. The ID is set as the ``session_id`` of the server tool span.
   Requests with an ``mcp-session-id`` header, and spans that already have a ``session_id``, are not changed.
   Alternatively, set this option with the ``DD_MCP_CAPTURE_SESSION`` environment variable.
   Default: ``False``
"""  # noqa: E501
