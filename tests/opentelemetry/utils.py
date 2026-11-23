from concurrent import futures
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import socket
from threading import Event
from threading import Thread


def create_grpc_server(servicer, add_servicer_to_server, instrumented=True, port=4317):
    import grpc

    server_factory = grpc.server if instrumented else getattr(grpc.server, "__wrapped__", grpc.server)
    server = server_factory(futures.ThreadPoolExecutor(max_workers=1))
    add_servicer_to_server(servicer, server)
    assert server.add_insecure_port("[::]:%d" % port) == port
    return server


def metric_names(requests):
    return {
        metric.name
        for request in requests
        for resource_metrics in request.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    }


@contextmanager
def capture_otlp_metrics(protocol):
    if protocol == "grpc":
        capture = _capture_grpc_metrics
    elif protocol == "http/protobuf":
        capture = _capture_http_metrics
    else:
        raise ValueError("Unsupported OTLP protocol: %s" % protocol)

    with capture() as requests:
        yield requests


@contextmanager
def capture_otlp_traces(protocol, port=None):
    if protocol == "grpc":
        capture = _capture_grpc_traces
        port = 4317 if port is None else port
    elif protocol == "http/protobuf":
        capture = _capture_http_traces
        port = 4318 if port is None else port
    else:
        raise ValueError("Unsupported OTLP protocol: %s" % protocol)

    with capture(port) as requests:
        yield requests


@contextmanager
def _capture_grpc_metrics():
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceResponse
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2_grpc import MetricsServiceServicer
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2_grpc import add_MetricsServiceServicer_to_server

    with _capture_grpc_requests(
        MetricsServiceServicer, add_MetricsServiceServicer_to_server, ExportMetricsServiceResponse, 4317
    ) as requests:
        yield requests


@contextmanager
def _capture_grpc_traces(port):
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2_grpc import TraceServiceServicer
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2_grpc import add_TraceServiceServicer_to_server

    with _capture_grpc_requests(
        TraceServiceServicer, add_TraceServiceServicer_to_server, ExportTraceServiceResponse, port
    ) as requests:
        yield requests


@contextmanager
def _capture_grpc_requests(servicer_type, add_servicer_to_server, response_type, port):
    class Service(servicer_type):
        def __init__(self):
            self.requests = []

        def Export(self, request, context):
            self.requests.append(request)
            return response_type()

    service = Service()
    server = create_grpc_server(service, add_servicer_to_server, instrumented=False, port=port)
    server.start()
    try:
        yield service.requests
    finally:
        server.stop(2).wait()


@contextmanager
def _capture_http_metrics():
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest

    with _capture_http_requests(4317, ExportMetricsServiceRequest) as requests:
        yield requests


@contextmanager
def _capture_http_traces(port):
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    with _capture_http_requests(port, ExportTraceServiceRequest) as requests:
        yield requests


class _CapturedHTTPRequests(list):
    port: int


@contextmanager
def _capture_http_requests(port, request_type=None, include_headers=False):
    requests = _CapturedHTTPRequests()

    class RequestHandler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if include_headers:
                request = (self.path, dict(self.headers), body)
            elif request_type is None:
                request = body
            else:
                request = request_type()
                request.ParseFromString(body)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            self.wfile.flush()
            requests.append(request)

        def log_message(self, format_string, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), RequestHandler)
    server.daemon_threads = False
    requests.port = server.server_port
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield requests
    finally:
        try:
            server.shutdown()
            thread.join(timeout=5)
        finally:
            server.server_close()
        assert not thread.is_alive(), "HTTP test receiver did not stop"


@contextmanager
def capture_otlp_signal(signal, protocol):
    if protocol == "http/protobuf":
        with _capture_http_requests(14319, include_headers=True) as requests:
            yield requests
        return

    if signal == "logs":
        from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceResponse
        from opentelemetry.proto.collector.logs.v1.logs_service_pb2_grpc import LogsServiceServicer
        from opentelemetry.proto.collector.logs.v1.logs_service_pb2_grpc import add_LogsServiceServicer_to_server

        servicer, add_servicer, response = (
            LogsServiceServicer,
            add_LogsServiceServicer_to_server,
            ExportLogsServiceResponse,
        )
    else:
        from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceResponse
        from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2_grpc import MetricsServiceServicer
        from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2_grpc import (
            add_MetricsServiceServicer_to_server,
        )

        servicer, add_servicer, response = (
            MetricsServiceServicer,
            add_MetricsServiceServicer_to_server,
            ExportMetricsServiceResponse,
        )

    with _capture_grpc_requests(servicer, add_servicer, response, 14317) as requests:
        yield requests


@contextmanager
def capture_dogstatsd_packets(port):
    """Capture the DogStatsD packets sent to the UDP port of localhost while the context is open."""
    packets = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", port))
    sock.settimeout(0.05)
    stopped = Event()

    def receive():
        while not stopped.is_set():
            try:
                packets.append(sock.recv(65535))
            except socket.timeout:
                pass

    thread = Thread(target=receive, daemon=True)
    thread.start()
    try:
        yield packets
    finally:
        stopped.set()
        thread.join()
        sock.setblocking(False)
        try:
            while True:
                packets.append(sock.recv(65535))
        except BlockingIOError:
            pass
        sock.close()
