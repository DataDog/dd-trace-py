"""
The Botocore integration will trace all AWS calls made with the botocore
library. Libraries like Boto3 that use Botocore will also be patched.

Enabling
~~~~~~~~

The botocore integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`patch_all()<patch_all>`.

Or use :ref:`patch()<patch>` to manually enable the integration::

    from ddtrace import patch
    patch(botocore=True)

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.botocore['distributed_tracing']

   Whether to inject distributed tracing data to requests in SQS and Lambda.

   Can also be enabled with the ``DD_BOTOCORE_DISTRIBUTED_TRACING`` environment variable.

   Default: ``True``

.. py:data:: ddtrace.config.botocore['clientcontext_custom_add_datadog_object']

    When a tracing a directly invoked lambda function, the trace context is added to the
    context.clientContext.custom field.
    The new method is to add the trace context directly to the custom field, i.e.
    ``custom{"datadog-trace-id"="12345"}``.
    When set to True, an object named "_datadog" containing the trace context is added
    to the context.clientContext.custom field.
    This is to preserve legacy behavior, and is not compatible with directly invoking Go
    or Java Lambda functions.

    Please note: setting this to True is required for propagating traces to Lambda
    functions instrumented with datadog-lambda-python < v41 or datadog-lambda-js < v3.57.0.

    Can also be enabled with the ``DD_BOTOCORE_CLIENTCONTEXT_CUSTOM_ADD_DATADOG_OBJECT``
    environment variable.

    Default: ``False``


Example::

    from ddtrace import config

    # Enable distributed tracing
    config.botocore['distributed_tracing'] = True

"""


from ...utils.importlib import require_modules


required_modules = ["botocore.client"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch

        __all__ = ["patch"]
