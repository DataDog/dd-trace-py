"""
The ``urllib3`` integration instruments tracing on http calls with optional
support for distributed tracing across services the client communicates with.


Enabling
~~~~~~~~

The ``urllib3`` integration is not enabled by default. Use either ``ddtrace-run``
or ``import ddtrace.auto`` with ``DD_PATCH_MODULES`` or ``DD_TRACE_URLLIB3_ENABLED`` to enable it.
``DD_PATCH_MODULES=urllib3 ddtrace-run python app.py`` or
``DD_PATCH_MODULES=urllib3:true python app.py``::

    import ddtrace.auto
    # use urllib3 like usual


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.urllib3['service']

   The service name reported by default for urllib3 client instances.

   This option can also be set with the ``DD_URLLIB3_SERVICE`` environment
   variable.

   Default: ``"urllib3"``


.. py:data:: ddtrace.config.urllib3['distributed_tracing']

   Whether or not to parse distributed tracing headers.

   Default: ``True``


.. py:data:: ddtrace.config.urllib3['trace_query_string']

   Whether or not to include the query string as a tag.

   Default: ``False``


.. py:data:: ddtrace.config.urllib3['split_by_domain']

   Whether or not to use the domain name of requests as the service name.

   Default: ``False``
"""
