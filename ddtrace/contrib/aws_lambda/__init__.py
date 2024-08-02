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

   Used to determine when to submit spans before a timeout occurs.
   When the remaining time in an AWS Lambda invocation is less than `DD_APM_FLUSH_DEADLINE_MILLISECONDS`,
   the tracer will attempt to submit the current active spans and all finished spans.

   Default: 100


For additional configuration refer to
`Instrumenting Python Serverless Applications by Datadog <https://docs.datadoghq.com/serverless/installation/python>`_.
"""
from ...internal.utils.importlib import require_modules


required_modules = ["aws_lambda"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        # Required to allow users to import from `ddtrace.contrib.aiohttp.patch` directly
        from . import patch as _  # noqa: F401, I001

        from ..internal.aws_lambda.patch import get_version
        from ..internal.aws_lambda.patch import patch
        from ..internal.aws_lambda.patch import unpatch

        __all__ = ["patch", "unpatch", "get_version"]
