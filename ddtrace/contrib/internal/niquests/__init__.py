"""
The niquests__ integration traces all HTTP requests made with the ``niquests`` library.

Enabling
~~~~~~~~

The ``niquests`` integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(niquests=True)

    # use niquests like usual


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.niquests['service']

   The service name for ``niquests`` requests.
   By default, the service name is set to ``"niquests"``.

   This option can also be set with the ``DD_NIQUESTS_SERVICE`` environment
   variable.

   Default: ``"niquests"``


.. py:data:: ddtrace.config.niquests['distributed_tracing']

   Whether or not to inject distributed tracing headers into requests.

   This option can also be set with the ``DD_NIQUESTS_DISTRIBUTED_TRACING`` environment
   variable.

   Default: ``True``


.. py:data:: ddtrace.config.niquests['split_by_domain']

   Whether or not to use the domain name of requests as the service name.

   This setting takes precedence over ``ddtrace.config.niquests['service']``.

   This option can also be set with the ``DD_NIQUESTS_SPLIT_BY_DOMAIN`` environment
   variable.

   Default: ``False``


:ref:`Headers tracing <http-headers-tracing>` is supported for this integration.

:ref:`HTTP Tagging <http-tagging>` is supported for this integration.

.. __: https://niquests.readthedocs.io/
"""
