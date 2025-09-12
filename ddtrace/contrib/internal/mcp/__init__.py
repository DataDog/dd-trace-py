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

Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.mcp["service"]
   The service name reported by default for MCP requests.
   Alternatively, you can set this option with the ``DD_SERVICE`` or ``DD_MCP_SERVICE`` environment
   variables.
   Default: ``DD_SERVICE``

.. py:data:: ddtrace.config.mcp["distributed_tracing"]
   Whether or not to enable distributed tracing for MCP requests.
   Alternatively, you can set this option with the ``DD_MCP_DISTRIBUTED_TRACING`` environment
   variable.
   Default: ``True``

Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the MCP integration on a per-instance basis use the
``Pin`` API::

    import mcp
    from ddtrace.trace import Pin

    Pin.override(mcp, service="my-mcp-service")
"""  # noqa: E501
