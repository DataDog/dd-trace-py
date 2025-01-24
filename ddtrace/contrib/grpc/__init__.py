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


Global Configuration
~~~~~~~~~~~~~~~~~~~~

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


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the gRPC integration on an per-channel basis use the
``Pin`` API::

    import grpc
    from ddtrace import patch
    from ddtrace.trace import Pin


    patch(grpc=True)

    # override the pin on the client
    Pin.override(grpc.Channel, service='mygrpc')
    with grpc.insecure_channel('localhost:50051') as channel:
        # create stubs and send requests
        pass

To configure the gRPC integration on the server use the ``Pin`` API::

    import grpc
    from grpc.framework.foundation import logging_pool

    from ddtrace import patch
    from ddtrace.trace import Pin

    patch(grpc=True)

    # override the pin on the server
    Pin.override(grpc.Server, service='mygrpc')
    server = grpc.server(logging_pool.pool(2))
    server.add_insecure_port('localhost:50051')
    add_MyServicer_to_server(MyServicer(), server)
    server.start()
"""


# Required to allow users to import from  `ddtrace.contrib.grpc.patch` directly
import warnings as _w


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001


from ddtrace.contrib.internal.grpc.patch import get_version  # noqa: F401
from ddtrace.contrib.internal.grpc.patch import patch  # noqa: F401
from ddtrace.contrib.internal.grpc.patch import unpatch  # noqa: F401
