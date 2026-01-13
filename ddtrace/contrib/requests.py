"""
The ``requests`` integration traces all HTTP requests made with the ``requests``
library.

The default service name used is `requests` but it can be configured to match
the services that the specific requests are made to.

Enabling
~~~~~~~~

The requests integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(requests=True)

    # use requests like usual


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.requests['service']

   The service name reported by default for requests queries. This value will
   be overridden if the split_by_domain setting is enabled.

   This option can also be set with the ``DD_REQUESTS_SERVICE`` environment
   variable.

   Default: ``"requests"``


    .. _requests-config-distributed-tracing:
.. py:data:: ddtrace.config.requests['distributed_tracing']

   Whether or not to parse distributed tracing headers.

   Default: ``True``


.. py:data:: ddtrace.config.requests['trace_query_string']

   Whether or not to include the query string as a tag.

   Default: ``False``


.. py:data:: ddtrace.config.requests['split_by_domain']

   Whether or not to use the domain name of requests as the service name.

   Default: ``False``
"""

from ddtrace.contrib.internal.requests.patch import TracedSession


__all__ = ["TracedSession"]
