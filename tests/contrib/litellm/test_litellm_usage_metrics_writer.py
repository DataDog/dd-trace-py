import errno
import http.server
import os
import socket
import tempfile
import threading
import time

import pytest

from ddtrace.contrib.internal.litellm import _usage_metrics_writer
from ddtrace.contrib.internal.litellm._usage_metrics import PROVIDER_ATTEMPT
from ddtrace.contrib.internal.litellm._usage_metrics_writer import UsageMetricsWriter
from ddtrace.contrib.internal.litellm._usage_metrics_writer import _parse_headers
from ddtrace.contrib.internal.litellm._usage_metrics_writer import ai_usage
from ddtrace.internal.settings import _core as settings_core
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.settings._agentless import config as agentless_config
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
    # Statuses for the next requests, before status applies again.
    statuses = []
    retry_after = None
    # Where a 3xx response points.
    location = None
    # Seconds the collector waits before it answers.
    delay = 0

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        Collector.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        time.sleep(Collector.delay)
        status = Collector.statuses.pop(0) if Collector.statuses else Collector.status
        self.send_response(status)
        if Collector.retry_after is not None and status >= 400:
            self.send_header("Retry-After", Collector.retry_after)
        if Collector.location is not None and 300 <= status < 400:
            self.send_header("Location", Collector.location)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def collector():
    Collector.requests = []
    Collector.status = 200
    Collector.statuses = []
    Collector.retry_after = None
    Collector.location = None
    Collector.delay = 0
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
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_HEADERS", "dd-api-key=abc,x-team=a%2Cb")
    return url


@pytest.fixture
def sleeps(monkeypatch):
    """Record the writer's retry delays instead of waiting."""
    delays = []

    def wait(stopping, seconds):
        delays.append(seconds)
        return stopping.is_set()

    monkeypatch.setattr(_usage_metrics_writer, "_wait", wait)
    return delays


def test_the_metrics_timeout_falls_back_to_the_global_one(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_METRICS_TIMEOUT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TIMEOUT", raising=False)
    assert _usage_metrics_writer._timeout_ms() == 10_000
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "2500")
    assert _usage_metrics_writer._timeout_ms() == 2_500
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_TIMEOUT", "4000")
    assert _usage_metrics_writer._timeout_ms() == 4_000


@pytest.mark.parametrize("value", ["0", "-5", "2.5", "soon"])
def test_a_metrics_timeout_that_is_not_positive_gives_the_default(monkeypatch, value):
    warnings = []
    monkeypatch.setattr(_usage_metrics_writer.log, "warning", lambda *args: warnings.append(args))
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_TIMEOUT", value)
    assert _usage_metrics_writer._timeout_ms() == 10_000
    assert [args[1] for args in warnings] == [value]


@pytest.mark.parametrize("source_name", ("LOCAL_CONFIG", "FLEET_CONFIG"))
def test_the_metrics_timeout_comes_from_stable_configuration(monkeypatch, source_name):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_METRICS_TIMEOUT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TIMEOUT", raising=False)
    monkeypatch.setitem(getattr(settings_core, source_name), "OTEL_EXPORTER_OTLP_METRICS_TIMEOUT", "4000")
    assert _usage_metrics_writer._timeout_ms() == 4_000
    # The metrics timeout from any source wins over the global one, wherever that comes from.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "2500")
    assert _usage_metrics_writer._timeout_ms() == 4_000


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


def test_a_wall_clock_set_back_does_not_invert_the_window(otlp_endpoint):
    metrics_service_pb2 = pytest.importorskip("opentelemetry.proto.collector.metrics.v1.metrics_service_pb2")
    writer = UsageMetricsWriter("otlp", interval=60, metrics=["gen_ai.client.inference.usage.input_tokens"])
    # As if the clock went back ten seconds since the window started.
    writer._window_start_ns = time.time_ns() + 10_000_000_000
    writer.record(PROVIDER_ATTEMPT, *observation(input_tokens=10))
    writer.flush()
    request = metrics_service_pb2.ExportMetricsServiceRequest()
    request.ParseFromString(Collector.requests[0][2])
    [point] = request.resource_metrics[0].scope_metrics[0].metrics[0].sum.data_points
    assert point.start_time_unix_nano <= point.time_unix_nano


def test_the_shutdown_deadline_is_capped(otlp_endpoint, monkeypatch):
    # A caller's deadline later than the shutdown time does not extend it.
    monkeypatch.setattr(_usage_metrics_writer, "_SHUTDOWN_SECONDS", 0.5)
    monkeypatch.setattr(_usage_metrics_writer, "_timeout_ms", lambda: 10_000)
    Collector.delay = 3
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    started = time.monotonic()
    writer.on_shutdown(time.monotonic() + 30)
    assert time.monotonic() - started < 2


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


def test_a_redirect_is_not_taken_for_delivery(otlp_endpoint, collector, monkeypatch, sleeps):
    # The native client does not follow redirects, so the export and its headers go nowhere else, and a 3xx response
    # is not the collector accepting the export.
    warnings = []
    monkeypatch.setattr(_usage_metrics_writer.log, "warning", lambda *args: warnings.append(args))
    Collector.status = 307
    Collector.location = f"http://127.0.0.1:{collector.server_address[1]}/elsewhere"
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    assert len(Collector.requests) == 1 and not writer._pending
    assert [args[2] for args in warnings] == [307]


@pytest.mark.parametrize("configured", [False, True])
def test_a_failing_export_warns(monkeypatch, sleeps, configured):
    # Only the default destination, the local Agent's OTLP receiver, gets the hint about enabling it.
    warnings = []
    monkeypatch.setattr(_usage_metrics_writer.log, "warning", lambda *args: warnings.append(args))
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", raising=False)
    monkeypatch.setattr(agentless_config, "enabled", False)
    if configured:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "http://127.0.0.1:9/v1/metrics")
    else:
        monkeypatch.setattr(otel_config.exporter, "TRACE_METRICS_ENDPOINT", "http://127.0.0.1:9/v1/metrics")
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    [args] = warnings
    assert ("DD_OTLP_CONFIG_RECEIVER_PROTOCOLS_HTTP_ENDPOINT" in args[0]) is not configured
    assert args[2] == "ConnectionFailedError"
    assert len(writer._pending) == 1


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
    # Each export is tried once, the kept one first; none can wait for a later flush.
    assert len(Collector.requests) == sent + 2
    assert len(sleeps) == waited
    assert not writer._pending


def test_shutdown_does_not_wait_behind_a_retrying_flush(otlp_endpoint):
    Collector.status, Collector.retry_after = 503, "5"
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    flushing = threading.Thread(target=writer.flush)
    flushing.start()
    while not Collector.requests:
        time.sleep(0.01)
    started = time.monotonic()
    writer.on_shutdown()
    flushing.join(5)
    # The flush in progress stops retrying instead of waiting out Retry-After twice.
    assert time.monotonic() - started < 3
    assert not flushing.is_alive()


def test_shutdown_time_is_bounded(otlp_endpoint, monkeypatch, sleeps):
    monkeypatch.setattr(_usage_metrics_writer, "_SHUTDOWN_SECONDS", 0)
    Collector.status = 503
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    sent = len(Collector.requests)
    writer.on_shutdown()
    # Out of time: what is left is dropped, not sent.
    assert len(Collector.requests) == sent
    assert not writer._pending


def test_shutdown_time_is_bounded_behind_a_collector_that_does_not_answer(otlp_endpoint, monkeypatch):
    monkeypatch.setattr(_usage_metrics_writer, "_SHUTDOWN_SECONDS", 0.5)
    monkeypatch.setattr(_usage_metrics_writer, "_timeout_ms", lambda: 10_000)
    Collector.delay = 3
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    started = time.monotonic()
    writer.on_shutdown()
    # The request at exit gets what is left of the shutdown time, not the export timeout.
    assert time.monotonic() - started < 2
    # Nor does the last flush wait out a periodic flush whose request the collector does not answer.
    writer.record(PROVIDER_ATTEMPT, *observation())
    flushing = threading.Thread(target=writer.flush, daemon=True)
    flushing.start()
    while len(Collector.requests) < 2:
        time.sleep(0.01)
    started = time.monotonic()
    writer.on_shutdown()
    assert time.monotonic() - started < 2


@pytest.mark.parametrize(
    "variables, grpc",
    [
        ({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4317", "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"}, True),
        ({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4317", "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL": "GRPC"}, True),
        ({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}, False),
        (
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4317",
                "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
                "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL": "http/protobuf",
            },
            False,
        ),
        # A metrics endpoint is read with the metrics protocol, which defaults to the global one.
        ({"OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "http://collector:4317", "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"}, True),
        (
            {
                "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "http://collector:4317",
                "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL": "grpc",
            },
            True,
        ),
        (
            {
                "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "http://collector:4318/v1/metrics",
                "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL": "http/protobuf",
                "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
            },
            False,
        ),
        # Without a configured endpoint, the metrics go to an OTLP/HTTP default.
        ({"OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"}, False),
    ],
)
def test_a_grpc_otlp_endpoint_is_recognized(monkeypatch, variables, grpc):
    for name in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    assert _usage_metrics_writer.otlp_endpoint_is_grpc() is grpc


@pytest.mark.parametrize("source_name", ("LOCAL_CONFIG", "FLEET_CONFIG"))
def test_a_grpc_protocol_from_stable_configuration_is_recognized(monkeypatch, source_name):
    for name in (
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    assert _usage_metrics_writer.otlp_endpoint_is_grpc() is False
    monkeypatch.setitem(getattr(settings_core, source_name), "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", "grpc")
    assert _usage_metrics_writer.otlp_endpoint_is_grpc() is True


@pytest.mark.parametrize("source_name", ("LOCAL_CONFIG", "FLEET_CONFIG"))
def test_the_endpoints_come_from_stable_configuration(collector, monkeypatch, source_name):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    source = getattr(settings_core, source_name)
    port = collector.server_address[1]
    # The global endpoint gets the metrics path; a metrics endpoint, even set in the environment, wins over it.
    monkeypatch.setitem(source, "OTEL_EXPORTER_OTLP_ENDPOINT", f"http://127.0.0.1:{port}/global")
    for expected in ("/global/v1/metrics", "/metrics-only"):
        if expected == "/metrics-only":
            monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", f"http://127.0.0.1:{port}/metrics-only")
        Collector.requests.clear()
        writer = UsageMetricsWriter("otlp", interval=60)
        writer.record(PROVIDER_ATTEMPT, *observation())
        writer.flush()
        [(sent_path, _, _)] = Collector.requests
        assert sent_path == expected


@pytest.fixture
def client_arguments(monkeypatch):
    """The arguments the OTLP sender builds its HTTP client with."""
    arguments = {}

    def client(base_url, **kwargs):
        arguments.update(kwargs, base_url=base_url)

    monkeypatch.setattr(_usage_metrics_writer, "HTTPClient", client)
    for name in ("ENDPOINT", "METRICS_ENDPOINT", "HEADERS", "METRICS_HEADERS"):
        monkeypatch.delenv(f"OTEL_EXPORTER_OTLP_{name}", raising=False)
    monkeypatch.setattr(agentless_config, "enabled", True)
    monkeypatch.setattr(agentless_config, "api_key", "secret-api-key")
    monkeypatch.setattr(otel_config.exporter, "TRACE_METRICS_ENDPOINT", "https://otlp.datadoghq.com/v1/metrics")
    return arguments


def test_agentless_sends_the_api_key_to_the_intake(client_arguments, monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_HEADERS", "x-team=a")
    _usage_metrics_writer._OtlpSender()
    assert client_arguments["base_url"] == "https://otlp.datadoghq.com"
    assert client_arguments["headers"] == [("x-team", "a"), ("dd-api-key", "secret-api-key")]


@pytest.mark.parametrize(
    "configured, sent",
    [
        # Only a header named dd-api-key, in any case, is the API key.
        ("x-dd-api-key=metadata", [("x-dd-api-key", "metadata"), ("dd-api-key", "secret-api-key")]),
        ("DD-API-KEY=mine", [("DD-API-KEY", "mine")]),
    ],
)
def test_agentless_matches_the_api_key_header_by_name(client_arguments, monkeypatch, configured, sent):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_HEADERS", configured)
    _usage_metrics_writer._OtlpSender()
    assert client_arguments["headers"] == sent


@pytest.mark.parametrize("source_name", ("LOCAL_CONFIG", "FLEET_CONFIG"))
@pytest.mark.parametrize("name", ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"))
def test_agentless_withholds_the_api_key_from_a_configured_endpoint(client_arguments, monkeypatch, source_name, name):
    monkeypatch.setitem(getattr(settings_core, source_name), name, "http://collector.example:4318")
    monkeypatch.setitem(getattr(settings_core, source_name), "OTEL_EXPORTER_OTLP_HEADERS", "authorization=Bearer t")
    _usage_metrics_writer._OtlpSender()
    assert client_arguments["base_url"] == "http://collector.example:4318"
    assert client_arguments["headers"] == [("authorization", "Bearer t")]


def test_the_logged_endpoint_has_no_secrets(collector, monkeypatch):
    port = collector.server_address[1]
    url = f"http://user:hunter2@127.0.0.1:{port}/token-123/v1/metrics?api_key=secret123"
    monkeypatch.setattr(otel_config.exporter, "TRACE_METRICS_ENDPOINT", url)
    warnings = []
    monkeypatch.setattr(_usage_metrics_writer.log, "warning", lambda *args: warnings.append(args))
    Collector.status = 400
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    [(sent_path, _, _)] = Collector.requests
    assert sent_path == "/token-123/v1/metrics?api_key=secret123"
    [args] = warnings
    logged = " ".join(str(arg) for arg in args)
    assert "hunter2" not in logged and "secret123" not in logged and "token-123" not in logged
    assert f"http://127.0.0.1:{port}" in logged


def test_close_spends_one_shutdown_budget(otlp_endpoint, monkeypatch):
    # Unpatching while a flush waits on a collector that does not answer.
    monkeypatch.setattr(_usage_metrics_writer, "_SHUTDOWN_SECONDS", 1.5)
    monkeypatch.setattr(_usage_metrics_writer, "_timeout_ms", lambda: 10_000)
    Collector.delay = 3
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.start()
    writer.record(PROVIDER_ATTEMPT, *observation())
    flushing = threading.Thread(target=writer.flush, daemon=True)
    flushing.start()
    while not Collector.requests:
        time.sleep(0.01)
    started = time.monotonic()
    writer.close()
    assert time.monotonic() - started < 2.5


def test_the_worker_shutdown_flush_keeps_the_close_deadline(otlp_endpoint, monkeypatch):
    monkeypatch.setattr(_usage_metrics_writer, "_SHUTDOWN_SECONDS", 0.5)
    monkeypatch.setattr(_usage_metrics_writer, "_timeout_ms", lambda: 10_000)
    Collector.delay = 3
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.start()
    writer.close()
    writer.record(PROVIDER_ATTEMPT, *observation())
    time.sleep(0.6)
    # What the worker runs as it stops, after close()'s deadline has passed: it does not start a new one.
    started = time.monotonic()
    writer.on_shutdown()
    assert time.monotonic() - started < 0.3


def test_the_global_endpoint_keeps_its_query(collector, monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", raising=False)
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT", f"http://127.0.0.1:{collector.server_address[1]}/custom/?tenant=a"
    )
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    [(sent_path, _, _)] = Collector.requests
    assert sent_path == "/custom/v1/metrics?tenant=a"


@pytest.mark.parametrize("path", ["/custom?tenant=a%20b", ""])
def test_the_metrics_endpoint_is_used_as_given(collector, monkeypatch, path):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", f"http://127.0.0.1:{collector.server_address[1]}{path}")
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
    sender = writer._otlp
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.reset()
    writer.flush()
    assert Collector.requests == []
    # A forked child does not reuse the parent's HTTP client.
    assert writer._otlp is not sender
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    assert len(Collector.requests) == 1


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
    # UDP drops what does not fit the receive buffer. Few enough series that one flush fits Linux's default buffer
    # even when the receiving thread falls behind on a busy host, and a larger buffer where the system allows it.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 22)
    sock.bind(("127.0.0.1", 0))
    monkeypatch.setattr(agent_config, "dogstatsd_url", f"udp://127.0.0.1:{sock.getsockname()[1]}")
    receiver = Receiver(sock)
    writer = UsageMetricsWriter("dogstatsd", interval=60)
    for i in range(10):
        writer.record(PROVIDER_ATTEMPT, *observation(user=f"user-{i:03d}"))
    writer.flush()
    packets = receiver.stop()
    assert len(packets) > 1
    assert all(len(packet) <= _usage_metrics_writer._UDP_MAX_PACKET for packet in packets)
    lines = [line for packet in packets for line in packet.decode().split("\n")]
    # 10 series of each counter and the two companions of the duration, plus 10 duration samples.
    costs = [line for line in lines if line.startswith("trajectory.gen_ai.client.inference.usage.cost:")]
    assert len(costs) == 10
    assert all(line.startswith("trajectory.gen_ai.client.inference.usage.cost:0.0000057|c|#") for line in costs)
    assert len([line for line in lines if line.startswith("gen_ai.client.inference.duration:1.5|d|#")]) == 10
    assert all("trajectory.profile:gen_ai.client.provider_attempt/0.1.0" in line for line in lines)
    assert all("user.id:user-" in line for line in lines)


def test_dogstatsd_stops_sending_at_the_shutdown_deadline(monkeypatch):
    # A full Unix socket can make each packet wait for its send timeout: the exit flush must not outlast close().
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    monkeypatch.setattr(agent_config, "dogstatsd_url", f"udp://127.0.0.1:{sock.getsockname()[1]}")
    receiver = Receiver(sock)
    writer = UsageMetricsWriter("dogstatsd", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation(user="user-late"))
    writer.flush(deadline=time.monotonic() - 1)
    writer.record(PROVIDER_ATTEMPT, *observation(user="user-on-time"))
    writer.flush(deadline=time.monotonic() + 30)
    lines = [line for packet in receiver.stop() for line in packet.decode().split("\n")]
    assert lines and all("user.id:user-on-time" in line for line in lines)


def test_dogstatsd_host_that_resolves_to_ipv6_only(monkeypatch):
    sock = None
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        sock.bind(("::1", 0))
    except OSError:
        if sock is not None:
            sock.close()
        pytest.skip("IPv6 loopback is not available")
    resolve = socket.getaddrinfo
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: resolve("::1" if host == "dogstatsd.test" else host, *args, **kwargs),
    )
    monkeypatch.setattr(agent_config, "dogstatsd_url", f"udp://dogstatsd.test:{sock.getsockname()[1]}")
    receiver = Receiver(sock)
    writer = UsageMetricsWriter("dogstatsd", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    assert receiver.stop()


def test_dogstatsd_prefers_ipv4_when_a_host_resolves_to_both(monkeypatch):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: [
            (socket.AF_INET6, socket.SOCK_DGRAM, 17, "", ("::1", port, 0, 0)),
            (socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("127.0.0.1", port)),
        ],
    )
    monkeypatch.setattr(agent_config, "dogstatsd_url", f"udp://dogstatsd.test:{port}")
    receiver = Receiver(sock)
    writer = UsageMetricsWriter("dogstatsd", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    assert receiver.stop()


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


def test_dogstatsd_moves_to_the_next_address_after_a_failed_send(monkeypatch):
    # A UDP connect succeeds without a listener: only a failed send shows that the first address is wrong.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    listening = sock.getsockname()[1]
    unused = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    unused.bind(("127.0.0.1", 0))
    closed = unused.getsockname()[1]
    unused.close()
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("127.0.0.1", closed)),
            (socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("127.0.0.1", listening)),
        ],
    )
    monkeypatch.setattr(agent_config, "dogstatsd_url", "udp://dogstatsd.test:8125")
    receiver = Receiver(sock)
    writer = UsageMetricsWriter("dogstatsd", interval=60)
    for _ in range(4):
        writer.record(PROVIDER_ATTEMPT, *observation())
        writer.flush()
        time.sleep(0.05)
    assert receiver.stop()


class SizeLimitedSocket:
    """A datagram socket that takes datagrams of up to limit bytes, as a Unix socket does on macOS (2 KiB)."""

    def __init__(self, limit):
        self.limit = limit
        self.sent = []
        self.closed = False

    def send(self, data):
        if len(data) > self.limit:
            raise OSError(errno.EMSGSIZE, "Message too long")
        self.sent.append(data)

    def close(self):
        self.closed = True


def test_dogstatsd_lines_too_long_to_send_are_dropped_alone(monkeypatch):
    monkeypatch.setattr(agent_config, "dogstatsd_url", "unix:///nonexistent/dsd.sock")
    sender = _usage_metrics_writer._DogStatsdSender()
    sock = SizeLimitedSocket(2048)
    sender._socket = sock
    sender.send(["a" * 3000, "b" * 10, "c" * 10_000, "d" * 10])
    # Longer than the socket takes, or than the Agent reads: dropped, while the other lines and the socket are kept.
    assert b"\n".join(sock.sent).split(b"\n") == [b"b" * 10, b"d" * 10]
    assert not sock.closed and sender._socket is sock


def test_close_flushes_and_releases_the_socket(monkeypatch):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    monkeypatch.setattr(agent_config, "dogstatsd_url", f"udp://127.0.0.1:{sock.getsockname()[1]}")
    receiver = Receiver(sock)
    writer = UsageMetricsWriter("dogstatsd", interval=60)
    writer.start()
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.close()
    assert receiver.stop()
    assert writer._dogstatsd._socket is None


def test_dogstatsd_without_a_listener_never_raises(monkeypatch):
    monkeypatch.setattr(agent_config, "dogstatsd_url", "unix:///nonexistent/dsd.sock")
    writer = UsageMetricsWriter("dogstatsd", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
