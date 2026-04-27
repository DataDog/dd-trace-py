"""
The AWS Durable Execution SDK integration instruments the
``aws-durable-execution-sdk-python`` library to trace durable workflow
executions and the operations invoked on a ``DurableContext`` (``step``,
``invoke``, ``wait``, ``map``, ``parallel``, etc.).

Spans are tagged with ``service``, ``env``, and ``version`` (see the
`Unified Service Tagging docs
<https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_)
along with the following integration-specific tags:

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
"""
