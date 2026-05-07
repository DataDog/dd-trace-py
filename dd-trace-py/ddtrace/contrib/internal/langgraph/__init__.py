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

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.langgraph["service"]
   The service name reported by default for LangGraph requests.
   Alternatively, set this option with the ``DD_LANGGRAPH_SERVICE`` environment variable.
"""
