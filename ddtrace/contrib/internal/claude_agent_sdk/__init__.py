"""
This integration instruments the ``claude-agent-sdk`` library.

Enabling
~~~~~~~~

The claude_agent_sdk integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(claude_agent_sdk=True)
    import claude_agent_sdk
    ...

Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.claude_agent_sdk["service"]

   The service name reported by default for claude_agent_sdk spans.

   This option can also be set with the ``DD_CLAUDE_AGENT_SDK_SERVICE`` environment
   variable.
"""
