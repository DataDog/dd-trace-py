"""
The httpx__ integration traces all HTTP requests made with the ``httpx``
library.

Enabling
~~~~~~~~

The ``httpx`` integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(httpx=True)

    # use httpx like usual


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.httpx['service']

   The default service name for ``httpx`` requests.
   By default the ``httpx`` integration will not define a service name and inherit
   its service name from its parent span.

   If you are making calls to uninstrumented third party applications you can
   set this setting or use the ``ddtrace.config.httpx['split_by_domain']`` setting.

   This option can also be set with the ``DD_HTTPX_SERVICE`` environment
   variable.

   Default: ``None``


.. py:data:: ddtrace.config.httpx['distributed_tracing']

   Whether or not to inject distributed tracing headers into requests.

   Default: ``True``


.. py:data:: ddtrace.config.httpx['split_by_domain']

   Whether or not to use the domain name of requests as the service name.

   This setting takes precedence over ``ddtrace.config.httpx['service']``

   Default: ``False``

:ref:`Headers tracing <http-headers-tracing>` is supported for this integration.

:ref:`HTTP Tagging <http-tagging>` is supported for this integration.

.. __: https://www.python-httpx.org/
"""
