"""
The LangGraph integration instruments graph invocation calls made using the LangGraph framework.

All traces submitted from the LangGraph integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.


Enabling
~~~~~~~~

The LangGraph integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the Vertex AI integration::

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
    from ddtrace import Pin, config

    Pin.override(langgraph, service="my-langgraph-service")
"""  # noqa: E501

from ddtrace.internal.utils.importlib import require_modules


required_modules = ["langgraph"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from ddtrace.contrib.internal.langgraph.patch import get_version
        from ddtrace.contrib.internal.langgraph.patch import patch
        from ddtrace.contrib.internal.langgraph.patch import unpatch

        __all__ = ["patch", "unpatch", "get_version"]
