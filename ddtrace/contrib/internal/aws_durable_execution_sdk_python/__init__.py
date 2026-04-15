"""
The AWS Durable Execution SDK integration instruments the
``aws-durable-execution-sdk-python`` library to trace durable workflow
executions, step executions, and cross-function invocations.

All traces submitted from this integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs
  <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.
- ``aws.durable_execution.arn``: The ARN of the durable execution.
- ``aws.durable_execution.replay_status``: Whether the execution is NEW or REPLAY.
- ``aws.durable_execution.step.name``: The name of the step being executed.
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

   Alternatively, set this option with the ``DD_SERVICE`` or
   ``DD_AWS_DURABLE_EXECUTION_SDK_PYTHON_SERVICE`` environment variable.

   Default: ``"aws_durable_execution_sdk_python"``
"""
