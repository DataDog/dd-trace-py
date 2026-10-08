import http.server
import os
import socket
import tempfile
import threading

import pytest

from ddtrace.contrib.internal.litellm import _usage_metrics_writer
from ddtrace.contrib.internal.litellm._usage_metrics import PROVIDER_ATTEMPT
from ddtrace.contrib.internal.litellm._usage_metrics_writer import UsageMetricsWriter
from ddtrace.contrib.internal.litellm._usage_metrics_writer import _parse_headers
from ddtrace.contrib.internal.litellm._usage_metrics_writer import ai_usage
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.settings._opentelemetry import otel_config


pytestmark = pytest.mark.skipif(ai_usage is None, reason="native ai_usage module not built")


def observation(user="u1", input_tokens=12):
    return {
        "operation_name": "chat",
        "provider_name": "openai",
        "request_model": "gpt-4o-mini",
        "duration_seconds": 1.5,
        "streaming": False,
        "input_tokens": input_tokens,
        "output_tokens": 7,
        "cost_usd": 5.7e-6,
        "cost_source": "estimated",
        "observation_point": "gateway",
    }, {"user.id": user}


class Collector(http.server.BaseHTTPRequestHandler):
    requests = []
    status = 200

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        Collector.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        self.send_response(Collector.status)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def collector():
    Collector.requests = []
    Collector.status = 200
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def otlp_endpoint(collector, monkeypatch):
    url = f"http://127.0.0.1:{collector.server_address[1]}/v1/metrics"
    monkeypatch.setattr(otel_config.exporter, "TRACE_METRICS_ENDPOINT", url)
    monkeypatch.setattr(otel_config.exporter, "METRICS_HEADERS", "dd-api-key=abc,x-team=a%2Cb")
    return url


def test_parse_headers():
    assert _parse_headers("a=1, b = two ,bad,=x,c=a%2Cb") == [("a", "1"), ("b", "two"), ("c", "a,b")]
    assert _parse_headers("") == []


def test_otlp_export_posts_protobuf_with_headers(otlp_endpoint):
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.record(PROVIDER_ATTEMPT, *observation(user="u2"))
    writer.flush()
    [(path, headers, body)] = Collector.requests
    assert path == "/v1/metrics"
    assert headers["content-type"] == "application/x-protobuf"
    assert headers["dd-api-key"] == "abc"
    assert headers["x-team"] == "a,b"
    for text in (b"gen_ai.client.inference.duration", b"trajectory.profile", PROVIDER_ATTEMPT.encode(), b"u1", b"u2"):
        assert text in body
    # Nothing new: nothing is sent.
    writer.flush()
    assert len(Collector.requests) == 1


def test_otlp_export_decodes(otlp_endpoint):
    metrics_service_pb2 = pytest.importorskip("opentelemetry.proto.collector.metrics.v1.metrics_service_pb2")
    writer = UsageMetricsWriter("otlp", interval=60, metrics=["gen_ai.client.inference.usage.input_tokens"])
    writer.record(PROVIDER_ATTEMPT, *observation(input_tokens=10))
    writer.record(PROVIDER_ATTEMPT, *observation(input_tokens=32))
    writer.flush()
    request = metrics_service_pb2.ExportMetricsServiceRequest()
    request.ParseFromString(Collector.requests[0][2])
    [scope_metrics] = request.resource_metrics[0].scope_metrics
    assert scope_metrics.scope.name == "ddtrace.contrib.litellm"
    assert [(a.key, a.value.string_value) for a in scope_metrics.scope.attributes] == [
        ("trajectory.profile", PROVIDER_ATTEMPT)
    ]
    [metric] = scope_metrics.metrics
    assert metric.name == "gen_ai.client.inference.usage.input_tokens"
    assert metric.sum.is_monotonic
    assert metric.sum.aggregation_temporality == 1  # delta
    [point] = metric.sum.data_points
    assert point.as_int == 42
    assert point.start_time_unix_nano < point.time_unix_nano


def test_otlp_export_failures_never_raise(otlp_endpoint, collector, monkeypatch):
    Collector.status = 500
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    assert len(Collector.requests) == 1

    monkeypatch.setattr(otel_config.exporter, "TRACE_METRICS_ENDPOINT", "http://127.0.0.1:9/v1/metrics")
    unreachable = UsageMetricsWriter("otlp", interval=60)
    unreachable.record(PROVIDER_ATTEMPT, *observation())
    unreachable.flush()


def test_rejected_observations_are_dropped(otlp_endpoint):
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, {"operation_name": "chat"}, None)
    writer.record(PROVIDER_ATTEMPT, {"operation_name": object()}, None)
    writer.flush()
    assert Collector.requests == []


def test_reset_drops_the_parent_points(otlp_endpoint):
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.reset()
    writer.flush()
    assert Collector.requests == []


def test_on_shutdown_flushes(otlp_endpoint):
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.on_shutdown()
    assert len(Collector.requests) == 1


class Receiver:
    """Drains a datagram socket on its own thread, as the Agent does."""

    def __init__(self, sock):
        self.sock = sock
        self.packets = []
        sock.settimeout(0.5)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while True:
            try:
                self.packets.append(self.sock.recv(65535))
            except socket.timeout:
                if getattr(self, "done", False):
                    return
            except OSError:
                return

    def stop(self):
        self.done = True
        self.thread.join()
        self.sock.close()
        return self.packets


def test_dogstatsd_over_udp_splits_packets(monkeypatch):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    monkeypatch.setattr(agent_config, "dogstatsd_url", f"udp://127.0.0.1:{sock.getsockname()[1]}")
    receiver = Receiver(sock)
    writer = UsageMetricsWriter("dogstatsd", interval=60)
    for i in range(50):
        writer.record(PROVIDER_ATTEMPT, *observation(user=f"user-{i:03d}"))
    writer.flush()
    packets = receiver.stop()
    assert len(packets) > 1
    assert all(len(packet) <= _usage_metrics_writer._UDP_MAX_PACKET for packet in packets)
    lines = [line for packet in packets for line in packet.decode().split("\n")]
    # 50 series of each counter and the two companions of the duration, plus 50 duration samples.
    costs = [line for line in lines if line.startswith("trajectory.gen_ai.client.inference.usage.cost:")]
    assert len(costs) == 50
    assert all(line.startswith("trajectory.gen_ai.client.inference.usage.cost:0.0000057|c|#") for line in costs)
    assert len([line for line in lines if line.startswith("gen_ai.client.inference.duration:1.5|d|#")]) == 50
    assert all("trajectory.profile:gen_ai.client.provider_attempt/0.1.0" in line for line in lines)
    assert all("user.id:user-" in line for line in lines)


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="Unix sockets are not available")
def test_dogstatsd_over_unix_socket(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(), "dsd.sock")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(path)
    monkeypatch.setattr(agent_config, "dogstatsd_url", f"unix://{path}")
    receiver = Receiver(sock)
    writer = UsageMetricsWriter("dogstatsd", interval=60)
    for i in range(20):
        writer.record(PROVIDER_ATTEMPT, *observation(user=f"user-{i:03d}"))
    writer.flush()
    packets = receiver.stop()
    lines = b"\n".join(packets).decode().split("\n")
    # Every line arrives, even where the socket takes smaller datagrams than 8 KiB (2 KiB on macOS).
    inputs = [line for line in lines if line.startswith("gen_ai.client.inference.usage.input_tokens:12|c|#")]
    assert len(inputs) == 20


def test_dogstatsd_without_a_listener_never_raises(monkeypatch):
    monkeypatch.setattr(agent_config, "dogstatsd_url", "unix:///nonexistent/dsd.sock")
    writer = UsageMetricsWriter("dogstatsd", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
