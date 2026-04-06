import os

import grpc
from grpc._grpcio_metadata import __version__ as _GRPC_VERSION
from grpc.framework.foundation import logging_pool

from ddtrace.contrib.internal.grpc.patch import patch
from ddtrace.contrib.internal.grpc.patch import unpatch
from tests.utils import TracerTestCase

from .hello_pb2_grpc import add_HelloServicer_to_server
from .hello_servicer import _HelloServicer


def _get_grpc_port():
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    try:
        worker_num = int(worker[2:])
    except (ValueError, IndexError):
        worker_num = 0
    return 50531 + worker_num


_GRPC_PORT = _get_grpc_port()
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
        self._server = grpc.server(self._server_pool)
        self._server.add_insecure_port("[::]:%d" % (_GRPC_PORT))
        add_HelloServicer_to_server(_HelloServicer(), self._server)
        self._server.start()

    def _stop_server(self):
        self._server.stop(None)
        self._server_pool.shutdown(wait=True)
