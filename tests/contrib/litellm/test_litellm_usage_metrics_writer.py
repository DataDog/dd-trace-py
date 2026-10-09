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
    # Statuses for the next requests, before ``status`` applies again.
    statuses = []
    retry_after = None

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        Collector.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        status = Collector.statuses.pop(0) if Collector.statuses else Collector.status
        self.send_response(status)
        if Collector.retry_after is not None and status >= 400:
            self.send_header("Retry-After", Collector.retry_after)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def collector():
    Collector.requests = []
    Collector.status = 200
    Collector.statuses = []
    Collector.retry_after = None
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


@pytest.fixture
def sleeps(monkeypatch):
    """Record the writer's retry delays instead of waiting."""
    delays = []
    monkeypatch.setattr(_usage_metrics_writer.time, "sleep", delays.append)
    return delays


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


def test_otlp_export_failures_never_raise(otlp_endpoint, collector, monkeypatch, sleeps):
    Collector.status = 500
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    assert len(Collector.requests) == 1

    monkeypatch.setattr(otel_config.exporter, "TRACE_METRICS_ENDPOINT", "http://127.0.0.1:9/v1/metrics")
    unreachable = UsageMetricsWriter("otlp", interval=60)
    unreachable.record(PROVIDER_ATTEMPT, *observation())
    unreachable.flush()


def test_retryable_failures_are_retried_within_a_flush(otlp_endpoint, sleeps):
    Collector.statuses = [503, 429]
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    bodies = [body for _, _, body in Collector.requests]
    assert len(bodies) == 3 and len(set(bodies)) == 1
    assert len(sleeps) == 2 and all(0 < delay <= 1.0 for delay in sleeps)
    writer.flush()
    assert len(Collector.requests) == 3


def test_an_export_that_keeps_failing_waits_for_the_next_flush(otlp_endpoint, sleeps):
    Collector.statuses = [503, 503, 503]
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation(user="user-first"))
    writer.flush()
    assert len(Collector.requests) == 3
    writer.record(PROVIDER_ATTEMPT, *observation(user="user-second"))
    writer.flush()
    # The kept export goes first, then the new one.
    first, second = [body for _, _, body in Collector.requests[3:]]
    assert first == Collector.requests[0][2] and b"user-first" in first
    assert b"user-second" in second and b"user-first" not in second
    writer.flush()
    assert len(Collector.requests) == 5


def test_a_refused_connection_keeps_the_export(otlp_endpoint, collector, monkeypatch, sleeps):
    monkeypatch.setattr(otel_config.exporter, "TRACE_METRICS_ENDPOINT", "http://127.0.0.1:9/v1/metrics")
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    assert len(writer._pending) == 1
    assert len(sleeps) == 2


def test_a_rejected_export_is_not_retried(otlp_endpoint, sleeps):
    Collector.status = 400
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    writer.flush()
    assert len(Collector.requests) == 1
    assert sleeps == []


def test_retry_after_is_honored_up_to_a_bound(otlp_endpoint, sleeps):
    writer = UsageMetricsWriter("otlp", interval=60)
    Collector.statuses, Collector.retry_after = [503], "0.25"
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    Collector.statuses, Collector.retry_after = [429], "120"
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    assert sleeps == [0.25, 5.0]


def test_pending_exports_are_bounded(otlp_endpoint, monkeypatch, sleeps):
    monkeypatch.setattr(_usage_metrics_writer, "_MAX_PENDING_EXPORTS", 2)
    Collector.status = 503
    writer = UsageMetricsWriter("otlp", interval=60)
    for user in ("user-one", "user-two", "user-three"):
        writer.record(PROVIDER_ATTEMPT, *observation(user=user))
        writer.flush()
    Collector.status = 200
    sent = len(Collector.requests)
    writer.flush()
    bodies = [body for _, _, body in Collector.requests[sent:]]
    # The oldest export was dropped to keep the two newest.
    assert len(bodies) == 2
    assert b"user-two" in bodies[0] and b"user-three" in bodies[1]
    assert not any(b"user-one" in body for body in bodies)


def test_on_shutdown_tries_pending_exports_once_without_waiting(otlp_endpoint, sleeps):
    Collector.status = 503
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    sent, waited = len(Collector.requests), len(sleeps)
    writer.record(PROVIDER_ATTEMPT, *observation(user="u2"))
    writer.on_shutdown()
    # The oldest export is tried once and still fails, so the newer one is not tried.
    assert len(Collector.requests) == sent + 1
    assert len(sleeps) == waited


@pytest.mark.parametrize("path", ["/custom?tenant=a%20b", ""])
def test_the_metrics_endpoint_is_used_as_given(collector, monkeypatch, path):
    url = f"http://127.0.0.1:{collector.server_address[1]}{path}"
    monkeypatch.setattr(otel_config.exporter, "TRACE_METRICS_ENDPOINT", url)
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    [(sent_path, _, _)] = Collector.requests
    assert sent_path == (path or "/")


def test_rejected_observations_are_dropped(otlp_endpoint):
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, {"operation_name": "chat"}, None)
    writer.record(PROVIDER_ATTEMPT, {"operation_name": object()}, None)
    writer.flush()
    assert Collector.requests == []


def test_rejections_and_issues_are_counted_in_telemetry(otlp_endpoint, monkeypatch):
    counts = []
    monkeypatch.setattr(
        _usage_metrics_writer.telemetry_writer,
        "add_count_metric",
        lambda namespace, name, value, tags: counts.append((namespace.value, name, value, dict(tags))),
    )
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, {"operation_name": "chat"}, None)
    inconsistent, _ = observation(input_tokens=20)
    inconsistent.update(input_basis="includes_cache", cache_read_input_tokens=80)
    writer.record(PROVIDER_ATTEMPT, inconsistent, None)
    rejected, issues = counts[0], counts[1:]
    assert rejected[:3] == ("tracers", "usage_metrics.observations_rejected", 1)
    assert rejected[3]["integration_name"] == "litellm"
    assert rejected[3]["profile"] == PROVIDER_ATTEMPT
    assert rejected[3]["code"]
    assert all(issue[:3] == ("tracers", "usage_metrics.observation_issues", 1) for issue in issues)
    assert "usage_inconsistent" in [issue[3]["code"] for issue in issues]


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
