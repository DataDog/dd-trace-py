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

This integration is configured automatically. The `datadog_lambda` package
calls ``patch_all`` when ``DD_TRACE_ENABLED`` is set to ``true``.
It's not recommended to call ``patch`` for it manually. Since it would not do
anything for other environments that do not meet the criteria above.


Configuration
~~~~~~~~~~~~~

.. important::

     You can configure some features with environment variables.

.. py:data:: DD_APM_FLUSH_DEADLINE_MILLISECONDS

   When to flush unfinished spans in an impending timeout.

   Default: AWS Lambda function timeout limit.


For additional configuration refer to
`Instrumenting Python Serverless Applications by Datadog <https://docs.datadoghq.com/serverless/installation/python>`_.
"""
from .patch import patch
from .patch import unpatch


__all__ = ["patch", "unpatch"]
