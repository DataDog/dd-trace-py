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

To verify that a connection can be made to the agent with your environment variable configurations run the command ``ddtrace-run --info``.


Failed to send traces... ``ConnectionRefusedError``
===================================================

``Failed to send traces to Datadog Agent...: ConnectionRefusedError(111, 'Connection refused')``

The most common error is a connection error. If you're experiencing a connection error, please make sure you've followed the setup
for your particular environment so that the tracer and Datadog agent are configured properly to connect, and that 
the Datadog agent is running: https://docs.datadoghq.com/tracing/setup_overview/setup/python/?tab=containers#configure-the-datadog-agent-for-apm

If the above doesn't fix your issue, the Datadog Agent also has a limit to the number of connections it can receive. This
can be configured with the instructions here: https://docs.datadoghq.com/tracing/troubleshooting/agent_rate_limits/#max-connection-limit.

Service, Env, or Version not set
================================
The ``service``, ``env``, and ``version`` tags are reserved tags in Datadog that allow unique capabilities in the Datadog UI for correlating and viewing data.
In order to have all of the features Datadog provides, we'd recommend setting these tags.

The ``service`` tag is used for the scoping of application specific data across metrics, traces, and logs. If a service tag is not provided for the tracer,
traces and trace metrics will appear under the name of the instrumented integration, e.g. `flask` for a Flask application.

The ``env`` tag is used for the scoping of the application's data to a specific environment, e.g. ``env:prod`` vs ``env:dev``.

For more information about the ``version`` tag please see: https://docs.datadoghq.com/tracing/deployment_tracking/#the-version-tag

To set ``service``, ``env``, and ``version`` properly for your environment, please see: https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging/?tab=kubernetes

Still having issues?
====================

If none of the above was able to resolve the issue then please reach out to
Datadog support at support@datadoghq.com. Or view the other support options
listed here: https://www.datadoghq.com/support/.
