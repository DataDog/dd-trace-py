===============
Troubleshooting
===============

Traces not showing up in the app
================================

The most common reason for traces not being received by Datadog is an agent
communication issue:

- Ensure the Datadog agent is running and reachable over the network if not on
  the same host.
- Ensure that ``ddtrace`` is configured with the hostname and port of the
  agent. See :ref:`Configuration` for the configuration variables.

To verify that a connection is being made to the agent, enable start-up logs
with the environment variable ``DD_TRACE_STARTUP_LOGS=true``. A diagnostic log
that looks like ``DATADOG TRACER DIAGNOSTIC...`` will be displayed with the
connection error if there is a problem connecting to the agent.


Failed to send traces... ``ConnectionRefusedError``
===================================================

``Failed to send traces to Datadog Agent...: ConnectionRefusedError(111, 'Connection refused')``

The Datadog Agent has a limit to the number of connections it can receive. This
can be configured with the instructions here: https://docs.datadoghq.com/tracing/troubleshooting/agent_rate_limits/#max-connection-limit.


Still having issues?
====================

If none of the above was able to resolve the issue then please reach out to
Datadog support at support@datadoghq.com. Or view the other support options
listed here: https://www.datadoghq.com/support/.
