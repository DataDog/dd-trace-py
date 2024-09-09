"""
The Botocore integration will trace all AWS calls made with the botocore
library. Libraries like Boto3 that use Botocore will also be patched.

Enabling
~~~~~~~~

The botocore integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(botocore=True)

To patch only specific botocore modules, pass a list of the module names instead::

    from ddtrace import patch
    patch(botocore=['s3', 'sns'])

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.botocore['distributed_tracing']

   Whether to inject distributed tracing data to requests in SQS, SNS, EventBridge, Kinesis Streams and Lambda.

   Can also be enabled with the ``DD_BOTOCORE_DISTRIBUTED_TRACING`` environment variable.

    Example::

        from ddtrace import config

        # Enable distributed tracing
        config.botocore['distributed_tracing'] = True


   Default: ``True``


.. py:data:: ddtrace.config.botocore['invoke_with_legacy_context']

    This preserves legacy behavior when tracing directly invoked Python and Node Lambda
    functions instrumented with datadog-lambda-python < v41 or datadog-lambda-js < v3.58.0.

    Legacy support for older libraries is available with
    ``ddtrace.config.botocore.invoke_with_legacy_context = True`` or by setting the environment
    variable ``DD_BOTOCORE_INVOKE_WITH_LEGACY_CONTEXT=true``.


    Default: ``False``


.. py:data:: ddtrace.config.botocore['operations'][<operation>].error_statuses = "<error statuses>"

    Definition of which HTTP status codes to consider for making a span as an error span.

    By default response status codes of ``'500-599'`` are considered as errors for all endpoints.

    Example marking 404, and 5xx as errors for ``s3.headobject`` API calls::

        from ddtrace import config

        config.botocore['operations']['s3.headobject'].error_statuses = '404,500-599'


    See :ref:`HTTP - Custom Error Codes<http-custom-error>` documentation for more examples.

.. py:data:: ddtrace.config.botocore['tag_no_params']

    This opts out of the default behavior of collecting a narrow set of API parameters as span tags.

    To not collect any API parameters, ``ddtrace.config.botocore.tag_no_params = True`` or by setting the environment
    variable ``DD_AWS_TAG_NO_PARAMS=true``.


    Default: ``False``


.. py:data:: ddtrace.config.botocore['instrument_internals']

    This opts into collecting spans for some internal functions, including ``parsers.ResponseParser.parse``.

    Can also be enabled with the ``DD_BOTOCORE_INSTRUMENT_INTERNALS`` environment variable.

    Default: ``False``


.. py:data:: ddtrace.config.botocore['span_prompt_completion_sample_rate']

   Configure the sample rate for the collection of bedrock prompts and completions as span tags.

   Alternatively, you can set this option with the ``DD_BEDROCK_SPAN_PROMPT_COMPLETION_SAMPLE_RATE`` environment
   variable.

   Default: ``1.0``


.. py:data:: (beta) ddtrace.config.botocore["span_char_limit"]

   Configure the maximum number of characters for bedrock span tags for prompt/response text.

   Text exceeding the maximum number of characters is truncated to the character limit
   and has ``...`` appended to the end.

   Alternatively, you can set this option with the ``DD_BEDROCK_SPAN_CHAR_LIMIT`` environment
   variable.

   Default: ``128``

"""


from ...internal.utils.importlib import require_modules


required_modules = ["botocore.client"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        # Required to allow users to import from `ddtrace.contrib.botocore.patch` directly
        from . import patch as _  # noqa: F401, I001

        from ..internal.botocore.patch import get_version
        from ..internal.botocore.patch import patch
        from ..internal.botocore.patch import patch_submodules

        __all__ = ["patch", "patch_submodules", "get_version"]
