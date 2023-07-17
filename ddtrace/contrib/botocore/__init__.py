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

To patch only specific botocore modules, pass a list of the module names instead::

    from ddtrace import patch
    patch(botocore=['s3', 'sns'])

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

.. py:data:: ddtrace.config.botocore['tag_all_params']

    **Deprecated**: This retains the deprecated behavior of adding span tags for
    all API parameters that are not explicitly excluded by the integration.
    These deprecated span tags will be added along with the API parameters
    enabled by default.

    This configuration is ignored if ``tag_no_parms`` (``DD_AWS_TAG_NO_PARAMS``)
    is set to ``True``.

    To collect all API parameters, ``ddtrace.config.botocore.tag_all_params =
    True`` or by setting the environment variable ``DD_AWS_TAG_ALL_PARAMS=true``.


    Default: ``False``

.. py:data:: ddtrace.config.botocore['instrument_internals']

    This opts into collecting spans for some internal functions, including ``parsers.ResponseParser.parse``.

    Can also be enabled with the ``DD_BOTOCORE_INSTRUMENT_INTERNALS`` environment variable.

    Default: ``False``


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
        from .patch import patch_submodules

        __all__ = ["patch", "patch_submodules"]
