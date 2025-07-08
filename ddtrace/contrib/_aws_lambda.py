"""
The aws_lambda integration currently enables traces to be sent
before an impending timeout in an AWS Lambda function instrumented with the
`Datadog Lambda Python <https://github.com/DataDog/datadog-lambda-python>`_ package.

Enabling
~~~~~~~~

The aws_lambda integration is enabled automatically for AWS Lambda
functions which have been instrumented with Datadog.


Global Configuration
~~~~~~~~~~~~~~~~~~~~

This integration is configured automatically when `ddtrace-run` or `import ddtrace.auto` is used.


Configuration
~~~~~~~~~~~~~

.. important::

     You can configure some features with environment variables.

.. py:data:: DD_APM_FLUSH_DEADLINE_MILLISECONDS

   Used to determine when to submit spans before a timeout occurs.
   When the remaining time in an AWS Lambda invocation is less than `DD_APM_FLUSH_DEADLINE_MILLISECONDS`,
   the tracer will attempt to submit the current active spans and all finished spans.

   Default: 100


For additional configuration refer to
`Instrumenting Python Serverless Applications by Datadog <https://docs.datadoghq.com/serverless/installation/python>`_.
"""
