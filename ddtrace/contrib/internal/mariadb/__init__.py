"""
The MariaDB integration instruments the
`MariaDB library <https://mariadb-corporation.github.io/mariadb-connector-python/usage.html>`_ to trace queries.


Enabling
~~~~~~~~

The MariaDB integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(mariadb=True)


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.mariadb["service"]

   The service name reported by default for MariaDB spans.

   This option can also be set with the ``DD_MARIADB_SERVICE`` environment
   variable.

   Default: ``"mariadb"``

"""
