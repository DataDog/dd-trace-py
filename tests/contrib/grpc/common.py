import grpc
from grpc._grpcio_metadata import __version__ as _GRPC_VERSION
from grpc.framework.foundation import logging_pool

from ddtrace.contrib.internal.grpc import constants
from ddtrace.contrib.internal.grpc.patch import patch
from ddtrace.contrib.internal.grpc.patch import unpatch
from ddtrace.trace import Pin
from tests.utils import TracerTestCase

from .hello_pb2_grpc import add_HelloServicer_to_server
from .hello_servicer import _HelloServicer


_GRPC_PORT = 50531
_GRPC_VERSION = tuple([int(i) for i in _GRPC_VERSION.split(".")])


class GrpcBaseTestCase(TracerTestCase):
    def setUp(self):
        super(GrpcBaseTestCase, self).setUp()
        patch()
        Pin._override(constants.GRPC_PIN_MODULE_SERVER, tracer=self.tracer)
        Pin._override(constants.GRPC_PIN_MODULE_CLIENT, tracer=self.tracer)
        self._start_server()

    def tearDown(self):
        self._stop_server()
        # Remove any remaining spans
        self.tracer.pop()
        # Unpatch grpc
        unpatch()
        super(GrpcBaseTestCase, self).tearDown()

    def _start_server(self):
        self._server_pool = logging_pool.pool(1)
        self._server = grpc.server(self._server_pool)
        self._server.add_insecure_port("[::]:%d" % (_GRPC_PORT))
        add_HelloServicer_to_server(_HelloServicer(), self._server)
        self._server.start()

    def _stop_server(self):
        self._server.stop(None)
        self._server_pool.shutdown(wait=True)
