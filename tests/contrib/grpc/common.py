import grpc
from grpc._grpcio_metadata import __version__ as _GRPC_VERSION
from grpc.framework.foundation import logging_pool

from ddtrace.contrib.internal.grpc.patch import patch
from ddtrace.contrib.internal.grpc.patch import unpatch
from tests.utils import TracerTestCase

from .hello_pb2_grpc import add_HelloServicer_to_server
from .hello_servicer import _HelloServicer


_GRPC_VERSION = tuple([int(i) for i in _GRPC_VERSION.split(".")])


class GrpcBaseTestCase(TracerTestCase):
    def setUp(self):
        super(GrpcBaseTestCase, self).setUp()
        patch()
        self._start_server()

    def tearDown(self):
        self._stop_server()
        # Remove any remaining spans
        self.pop_spans()
        # Unpatch grpc
        unpatch()
        super(GrpcBaseTestCase, self).tearDown()

    def _start_server(self):
        self._server_pool = logging_pool.pool(1)
        # Disable SO_REUSEPORT so a new server can never share a port with a not-yet-released
        # dying one, and bind to an ephemeral port ("[::]:0") so the OS atomically assigns a
        # guaranteed-free port.  add_insecure_port returns the chosen port.
        self._server = grpc.server(self._server_pool, options=(("grpc.so_reuseport", 0),))
        self._grpc_port = self._server.add_insecure_port("[::]:0")
        add_HelloServicer_to_server(_HelloServicer(), self._server)
        self._server.start()

    def _stop_server(self):
        # Wait for shutdown so the port is released before the next test rebinds it (avoids a
        # SO_REUSEPORT race where the new client hits the dying server, causing flaky failures).
        shutdown_complete = self._server.stop(None)
        shutdown_complete.wait()
        self._server_pool.shutdown(wait=True)
