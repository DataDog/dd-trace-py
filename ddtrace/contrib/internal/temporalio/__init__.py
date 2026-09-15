"""
The Temporal integration traces workflow and activity operations performed with
the ``temporalio`` library.

The integration traces workflow starts and signals as producer operations,
workflow queries as client operations, and activity execution as consumer
operations. Trace context is propagated through Temporal headers from client
operations to activities. Workflow execution itself is not traced because
Temporal may replay workflow code.


Enabling
~~~~~~~~

The ``temporalio`` integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch()<ddtrace.patch>` to manually enable the
integration::

    from ddtrace import patch

    patch(temporalio=True)


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.temporalio["distributed_tracing"]

   Propagate trace context through Temporal headers.

   This option can also be set with the
   ``DD_TEMPORALIO_DISTRIBUTED_TRACING`` environment variable.

   Default: ``True``
"""
