"""
The gRPC integration traces the client and server using the interceptor pattern.


Enabling
~~~~~~~~

The gRPC integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(grpc=True)

    # use grpc like usual


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.grpc["service"]

   The service name reported by default for gRPC client instances.

   This option can also be set with the ``DD_GRPC_SERVICE`` environment
   variable.

   Default: ``"grpc-client"``

.. py:data:: ddtrace.config.grpc_server["service"]

   The service name reported by default for gRPC server instances.

   This option can also be set with the ``DD_SERVICE`` or
   ``DD_GRPC_SERVER_SERVICE`` environment variables.

   Default: ``"grpc-server"``
"""
