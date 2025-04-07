"""
The LangGraph integration instruments the LangGraph Python library to emit traces for
graph and node invocations.

All traces submitted from the LangGraph integration are tagged by:
- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.

Enabling
~~~~~~~~

The LangGraph integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.
Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the LangGraph integration::
    from ddtrace import patch
    patch(langgraph=True)

Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.langgraph["service"]
   The service name reported by default for LangGraph requests.
   Alternatively, you can set this option with the ``DD_SERVICE`` or ``DD_LANGGRAPH_SERVICE`` environment
   variables.
   Default: ``DD_SERVICE``

Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the LangGraph integration on a per-instance basis use the
``Pin`` API::
    import langgraph
    from ddtrace import config
    from ddtrace.trace import Pin
    Pin.override(langgraph, service="my-langgraph-service")
"""
