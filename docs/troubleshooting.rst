===============
Troubleshooting
===============


Failed to send traces... ``ConnectionRefusedError``
===================================================

``Failed to send traces to Datadog Agent...: ConnectionRefusedError(111, 'Connection refused')``

The Datadog Agent has a limit to the number of connections it can receive. This
can be configured with the instructions `here
<https://docs.datadoghq.com/tracing/troubleshooting/agent_rate_limits/#max-connection-limit>`_.
