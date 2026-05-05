"""
The AWS Durable Execution SDK integration instruments the
``aws-durable-execution-sdk-python`` library to trace durable workflow
executions and the operations invoked on a ``DurableContext`` (``step``,
``invoke``, ``wait``, ``map``, ``parallel``, etc.).

Integration-specific tags:

- ``aws.durable.execute`` — the ``@durable_execution`` decorator
- ``aws.durable.step`` — ``DurableContext.step()``
- ``aws.durable.invoke`` — ``DurableContext.invoke()``
- ``aws.durable.wait`` — ``DurableContext.wait()``
- ``aws.durable.wait_for_condition`` — ``DurableContext.wait_for_condition()``
- ``aws.durable.wait_for_callback`` — ``DurableContext.wait_for_callback()``
- ``aws.durable.create_callback`` — ``DurableContext.create_callback()``
- ``aws.durable.map`` — ``DurableContext.map()``
- ``aws.durable.parallel`` — ``DurableContext.parallel()``
- ``aws.durable.child_context`` — ``DurableContext.run_in_child_context()``

All traces submitted from this integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs
  <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.
- ``aws.durable.execution_arn``: The ARN of the durable execution.
- ``aws_lambda.durable_execution.execution_name``: The Lambda function name extracted from the execution ARN.
- ``aws_lambda.durable_execution.execution_id``: The execution ID extracted from the execution ARN.
- ``aws.durable.replayed``: Whether the operation's terminal result was
  cached from a prior invocation (``"true"``) or not (``"false"``). Set on the
  execute span (derived from the durable context's replay status) and on every
  durable op span except ``wait_for_callback`` (which delegates internally to
  ``run_in_child_context``; the replay signal lands on the inner child_context
  span instead). On op spans, uses ``CheckpointedResult.is_succeeded()``.
- ``aws.durable.invocation_status``: The outcome of the workflow execution
  (``"succeeded"``, ``"failed"``, or ``"pending"``). Set on the execute span.
- ``aws.durable.invoke.function_name``: The target function name for invoke calls.


Enabling
~~~~~~~~

The AWS Durable Execution SDK integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(aws_durable_execution_sdk_python=True)
"""
