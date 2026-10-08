"""
The httpx2__ integration traces all HTTP requests made with the ``httpx2``
library.

Enabling
~~~~~~~~

The ``httpx2`` integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(httpx2=True)

    # use httpx2 like usual


Configuration
~~~~~~~~~~~~~

Use the following environment variables to configure the integration:

``DD_HTTPX2_SERVICE``
   The service name for ``httpx2`` requests. By default, requests inherit the
   service name from their parent span.

``DD_HTTPX2_DISTRIBUTED_TRACING``
   Whether to inject distributed tracing headers into requests. Defaults to
   ``True``.

``DD_HTTPX2_SPLIT_BY_DOMAIN``
   Whether to use the request domain as the service name. Defaults to
   ``False``.

:ref:`Headers tracing <http-headers-tracing>` is supported for this integration.

:ref:`HTTP Tagging <http-tagging>` is supported for this integration.

.. __: https://github.com/pydantic/httpx2
"""
