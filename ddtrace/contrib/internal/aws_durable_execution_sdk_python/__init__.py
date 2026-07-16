"""
The AWS Durable Execution SDK integration instruments the
``aws-durable-execution-sdk-python`` library to trace durable workflow
executions and the operations invoked on a ``DurableContext`` (``step``,
``invoke``, ``wait``, ``map``, ``parallel``, etc.).

Integration-specific tags:

- ``aws.durable.execution_arn``: ARN of the durable execution. Set on the
  execute span.
- ``aws.durable.invocation_status``: outcome of the workflow execution
  (``succeeded``, ``failed``, or ``pending``). Set on the execute span.
- ``aws.durable.replayed``: whether the operation's result was cached from a
  prior invocation. Set on the execute span and on op spans (except
  ``wait_for_callback``).
- ``aws.durable.invoke.function_name``: target function name. Set on
  ``invoke`` spans.


Enabling
~~~~~~~~

The AWS Durable Execution SDK integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(aws_durable_execution_sdk_python=True)


Distributed tracing across suspend/resume
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A durable workflow can pause via ``SuspendExecution`` and resume in a later
Lambda invocation. The integration keeps these invocations stitched into a
single trace by persisting the propagation headers in the durable execution
log.

On each suspend, the integration appends a synthetic STEP operation named
``_datadog_{N}`` whose payload is the current trace's Datadog propagation
headers. The next invocation reads back the highest-N checkpoint and resumes
the trace from there. No checkpoint is written for workflows that complete or
fail terminally.

Things to be aware of:

- The synthetic ``_datadog_*`` STEP operations appear in your durable execution
  log alongside user operations. They are deterministically named and only
  written when the propagation headers actually change since the last
  checkpoint.
- Only Datadog-style propagation headers are written; ``DD_TRACE_PROPAGATION_STYLE_INJECT``
  is ignored for these checkpoints since both writer and reader are Datadog.
- To opt out, set ``DD_DURABLE_CROSS_INVOCATION_TRACING_ENABLED=False``. The
  integration will stop writing new checkpoints but will continue to consume
  any that already exist on resumed workflows.
"""
