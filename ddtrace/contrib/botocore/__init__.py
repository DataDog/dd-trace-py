"""
The Botocore integration will trace all AWS calls made with the botocore
library. Libraries like Boto3 that use Botocore will also be patched.

Enabling
~~~~~~~~

The botocore integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :func:`patch_all()<ddtrace.patch_all>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(botocore=True)

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.botocore['distributed_tracing']

   Whether to inject distributed tracing data to requests in SQS, SNS, EventBridge, Kinesis Streams and Lambda.

   Can also be enabled with the ``DD_BOTOCORE_DISTRIBUTED_TRACING`` environment variable.

   Default: ``True``

.. py:data:: ddtrace.config.botocore['invoke_with_legacy_context']

    This preserves legacy behavior when tracing directly invoked Python and Node Lambda
    functions instrumented with datadog-lambda-python < v41 or datadog-lambda-js < v3.58.0.

    Legacy support for older libraries is available with
    ``ddtrace.config.botocore.invoke_with_legacy_context = True`` or by setting the environment
    variable ``DD_BOTOCORE_INVOKE_WITH_LEGACY_CONTEXT=true``.


    Default: ``False``


.. py:data:: ddtrace.config.botocore['s3_head_object_404_as_error']

    Whether any ``botocore.exceptions.ClientError`` exceptions raise for 404 responses to
    S3 ``HeadObject`` API calls should mark the representing span as an error.

    By default we mark these spans as having an exception.

    You can disable this behavior (ignore the exception) by configuring
    ``ddtrace.config.botocore.s3_head_object_404_as_error = True`` or by setting the environment
    variable ``DD_BOTOCORE_S3_HEAD_OBJECT_404_AS_ERROR=true``.


    Default: ``True``


Example::

    from ddtrace import config

    # Enable distributed tracing
    config.botocore['distributed_tracing'] = True

"""


from ...internal.utils.importlib import require_modules


required_modules = ["botocore.client"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch

        __all__ = ["patch"]
