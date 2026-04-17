"""
The AWS Durable Execution SDK integration instruments the
``aws-durable-execution-sdk-python`` library to trace durable workflow
executions, steps, invocations, and other operations.

Traced operations
~~~~~~~~~~~~~~~~~

- ``aws.durable_execution.execute`` — the ``@durable_execution`` decorator
- ``aws.durable_execution.step`` — ``DurableContext.step()``
- ``aws.durable_execution.invoke`` — ``DurableContext.invoke()``
- ``aws.durable_execution.wait`` — ``DurableContext.wait()``
- ``aws.durable_execution.wait_for_condition`` — ``DurableContext.wait_for_condition()``
- ``aws.durable_execution.wait_for_callback`` — ``DurableContext.wait_for_callback()``
- ``aws.durable_execution.create_callback`` — ``DurableContext.create_callback()``
- ``aws.durable_execution.map`` — ``DurableContext.map()``
- ``aws.durable_execution.parallel`` — ``DurableContext.parallel()``
- ``aws.durable_execution.child_context`` — ``DurableContext.run_in_child_context()``

All traces submitted from this integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs
  <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.
- ``aws.durable_execution.arn``: The ARN of the durable execution.
- ``aws.durable_execution.replay_status``: Whether the execution is NEW or REPLAY.
- ``aws.durable_execution.step.name``: The name of the step being executed.
- ``aws.durable_execution.step.replayed``: Whether the step was replayed from checkpoint (``"true"``) or executed fresh (``"false"``).
- ``aws.durable_execution.invoke.function_name``: The target function name for invoke calls.


Enabling
~~~~~~~~

The AWS Durable Execution SDK integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import config, patch

    patch(aws_durable_execution_sdk_python=True)


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.aws_durable_execution_sdk_python["service"]

   The service name reported by default for AWS Durable Execution SDK requests.

   The service name can be set via (in order of precedence):

   1. ``DD_DURABLE_EXECUTION_SERVICE`` environment variable (shared with Node.js)
   2. ``DD_AWS_DURABLE_EXECUTION_SDK_PYTHON_SERVICE`` environment variable
   3. ``DD_SERVICE`` environment variable
   4. Config default

   Default: ``"aws.durable_execution"``

.. py:data:: ddtrace.config.aws_durable_execution_sdk_python["distributed_tracing_enabled"]

   Whether to enable distributed tracing across chained Lambda invocations.
   When enabled, trace context is injected into ``invoke()`` payloads and
   extracted from the input event in ``@durable_execution`` handlers, linking
   traces across service boundaries.

   This option can also be set with the
   ``DD_AWS_DURABLE_EXECUTION_SDK_PYTHON_DISTRIBUTED_TRACING`` environment variable.

   Default: ``True``
"""
