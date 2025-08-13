"""
The aiobotocore integration will trace all AWS calls made with the ``aiobotocore``
library. This integration is not enabled by default.

Enabling
~~~~~~~~

The aiobotocore integration is not enabled by default. Use
:func:`patch()<ddtrace.patch>` to enable the integration::

    from ddtrace import patch
    patch(aiobotocore=True)

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.aiobotocore['tag_no_params']

    This opts out of the default behavior of adding span tags for a narrow set of API parameters.

    To not collect any API parameters, ``ddtrace.config.aiobotocore.tag_no_params = True`` or by setting the environment
    variable ``DD_AWS_TAG_NO_PARAMS=true``.


    Default: ``False``

"""
