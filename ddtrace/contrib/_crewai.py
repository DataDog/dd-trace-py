"""
The CrewAI integration instruments the CrewAI Python library to emit traces for crew/task/agent/tool executions.

All traces submitted from the CrewAI integration are tagged by:
- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.

Enabling
~~~~~~~~

The CrewAI integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the CrewAI integration::

    from ddtrace import patch

    patch(crewai=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.crewai["service"]

   The service name reported by default for CrewAI requests.

   Alternatively, you can set this option with the ``DD_SERVICE`` or ``DD_CREWAI_SERVICE`` environment
   variables.

   Default: ``DD_SERVICE``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the CrewAI integration on a per-instance basis use the
``Pin`` API::

    import crewai
    from ddtrace.trace import Pin

    Pin.override(crewai, service="my-crewai-service")
"""  # noqa: E501
