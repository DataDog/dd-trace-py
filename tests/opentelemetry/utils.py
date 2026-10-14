from concurrent import futures
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
from threading import Thread


def create_grpc_server(servicer, add_servicer_to_server, instrumented=True):
    import grpc

    server_factory = grpc.server if instrumented else getattr(grpc.server, "__wrapped__", grpc.server)
    server = server_factory(futures.ThreadPoolExecutor(max_workers=1))
    add_servicer_to_server(servicer, server)
    assert server.add_insecure_port("[::]:4317") == 4317
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
def _capture_grpc_metrics():
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceResponse
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2_grpc import MetricsServiceServicer
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2_grpc import add_MetricsServiceServicer_to_server

    class MetricsService(MetricsServiceServicer):
        def __init__(self):
            self.requests = []

        def Export(self, request, context):
            self.requests.append(request)
            return ExportMetricsServiceResponse()

    service = MetricsService()
    server = create_grpc_server(service, add_MetricsServiceServicer_to_server, instrumented=False)
    server.start()
    try:
        yield service.requests
    finally:
        server.stop(2).wait()


@contextmanager
def _capture_http_metrics():
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest

    requests = []

    class MetricsHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = ExportMetricsServiceRequest()
            request.ParseFromString(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format_string, *args):
            pass

    server = HTTPServer(("127.0.0.1", 4317), MetricsHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield requests
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
