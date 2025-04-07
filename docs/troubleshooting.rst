===============
Troubleshooting
===============

Installation failure
====================

``ModuleNotFoundError: No module named 'Cython'``

pip is failing to install ``ddtrace`` and complaining about a missing module (Cython).
``pip>=18`` is required to properly install ``ddtrace`` package.

Check which version of pip you are using with ``pip --version``.

Consider upgrading pip via ``pip install -U pip>=18``.

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


Root span is missing error details
==================================

If an error is raised during the execution of instrumented code, any span from instrumented code which does not handle the exception will include error details (error flag, message, type, traceback).

However, if the instrumented code for a span handles the exception, that span will not include error details as the exception is not raised from the call stack of the span's execution context.

This can be a problem for users who want to see error details from a child span in the root span of a trace. An example of this is when an error is raised during the execution of view code, subsequently handled by the web framework in order to return an error page, the error details from the span for an instrumented view will not be included in the root span for the request.

While this is default behavior for integrations, users can add a trace filter to propagate the error details up to the root span::

  from ddtrace.trace import Span, tracer
  from ddtrace.trace import TraceFilter


  class ErrorFilter(TraceFilter):
    def process_trace(self, trace):
        # Find first child span with an error and copy its error details to root span
        if not trace:
            return trace

        local_root = trace[0]

        for span in trace[1:]:
            if span.error == 1:  # or any other conditional for finding the relevant child span
                local_root.error = 1
                local_root.set_tags({
                    "error.msg": span.get_tag("error.msg"),
                    "error.type": span.get_tag("error.type"),
                    "error.stack": span.get_tag("error.stack"),
                })
                break

        return trace


  tracer.configure(trace_processors=[ErrorFilter()])


ModuleNotFoundError when running tests with riot
================================================
If you run a test and encounter this error ``ModuleNotFoundError: No module named '<package name>'``

Your base virtual environment was likely created without a package.
Remove all the ``.riot/venv*``  directories and run the tests without the -s option. 

``scripts/ddtest DD_TRACE_AGENT_URL=http://localhost:9126 riot -v run -p3.12 --pass-env <integration_name>``

This will re-create all your virtual environments and hopefully install package in the correct venv.


Still having issues?
====================

If none of the above was able to resolve the issue then please reach out to
Datadog support at support@datadoghq.com. Or view the other support options
listed here: https://www.datadoghq.com/support/.
