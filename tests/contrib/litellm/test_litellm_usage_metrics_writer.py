import datetime
import email.utils
import errno
import gzip
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
    sizes = []
    status = 200
    # Statuses for the next requests, before status applies again.
    statuses = []
    retry_after = None
    # Where a 3xx response points.
    location = None
    # Seconds the collector waits before it answers.
    delay = 0
    # The body of a 2xx response.
    response_body = b""

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        # Decoded as a collector does; the size sent is kept apart.
        Collector.sizes.append(len(body))
        if self.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        Collector.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        time.sleep(Collector.delay)
        status = Collector.statuses.pop(0) if Collector.statuses else Collector.status
        self.send_response(status)
        if Collector.retry_after is not None and status >= 400:
            self.send_header("Retry-After", Collector.retry_after)
        if Collector.location is not None and 300 <= status < 400:
            self.send_header("Location", Collector.location)
        body = Collector.response_body if 200 <= status < 300 else b""
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def collector():
    Collector.requests = []
    Collector.sizes = []
    Collector.status = 200
    Collector.statuses = []
    Collector.retry_after = None
    Collector.location = None
    Collector.delay = 0
    Collector.response_body = b""
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
    # The default destination is Datadog: the profile's version is set apart with "/".
    profile = PROVIDER_ATTEMPT.replace("@", "/").encode()
    for text in (b"gen_ai.client.inference.duration", b"trajectory.profile", profile, b"u1", b"u2"):
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
    # The default destination is Datadog, which keeps "/" and not "@" in tag values.
    assert [(a.key, a.value.string_value) for a in scope_metrics.scope.attributes] == [
        ("trajectory.profile", PROVIDER_ATTEMPT.replace("@", "/"))
    ]
    [metric] = scope_metrics.metrics
    assert metric.name == "gen_ai.client.inference.usage.input_tokens"
    assert metric.sum.is_monotonic
    assert metric.sum.aggregation_temporality == 1  # delta
    [point] = metric.sum.data_points
    assert point.as_int == 42
    assert point.start_time_unix_nano < point.time_unix_nano


COST = "trajectory.gen_ai.client.inference.usage.cost"


def _otlp_cost(body):
    metrics_service_pb2 = pytest.importorskip("opentelemetry.proto.collector.metrics.v1.metrics_service_pb2")
    request = metrics_service_pb2.ExportMetricsServiceRequest()
    request.ParseFromString(body)
    for scope_metrics in request.resource_metrics[0].scope_metrics:
        profile = scope_metrics.scope.attributes[0].value.string_value
        for metric in scope_metrics.metrics:
            if metric.name == COST:
                [point] = metric.sum.data_points
                value = point.as_double if point.HasField("as_double") else point.as_int
                return metric.unit, value, profile
    raise AssertionError("no cost metric")


def test_datadog_destinations_store_cost_as_dogstatsd_does(otlp_endpoint, monkeypatch):
    # With no endpoint configured, the export goes to the Agent's receiver or the intake, which store values as they
    # arrive: cost is sent in USD, the same value the DogStatsD exporter sends under the same name.
    writer = UsageMetricsWriter("otlp", interval=60, metrics=[COST])
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    unit, otlp_value, profile = _otlp_cost(Collector.requests[0][2])
    assert unit == "USD"
    assert profile == PROVIDER_ATTEMPT.replace("@", "/")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    monkeypatch.setattr(agent_config, "dogstatsd_url", f"udp://127.0.0.1:{sock.getsockname()[1]}")
    receiver = Receiver(sock)
    dogstatsd = UsageMetricsWriter("dogstatsd", interval=60, metrics=[COST])
    dogstatsd.record(PROVIDER_ATTEMPT, *observation())
    dogstatsd.record(PROVIDER_ATTEMPT, *observation())
    dogstatsd.flush()
    [line] = [line for packet in receiver.stop() for line in packet.decode().split("\n")]
    assert line.startswith(COST + ":")
    assert otlp_value == float(line.split(":", 1)[1].split("|", 1)[0])
    assert otlp_value == pytest.approx(2 * 5.7e-6)


def test_a_configured_endpoint_gets_the_opentelemetry_encoding(collector, monkeypatch):
    # A collector converts units itself: it gets the OpenTelemetry encoding, cost in integer nano-USD.
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", f"http://127.0.0.1:{collector.server_address[1]}/v1/metrics"
    )
    writer = UsageMetricsWriter("otlp", interval=60, metrics=[COST])
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    assert _otlp_cost(Collector.requests[0][2]) == ("{nanoUSD}", 5700, PROVIDER_ATTEMPT)


@pytest.mark.parametrize(
    "url, datadog",
    [
        ("https://otlp.datadoghq.com/v1/metrics", True),
        ("https://otlp.us5.datadoghq.com/v1/metrics", True),
        ("https://OTLP.datadoghq.eu/v1/metrics", True),
        ("https://otlp.ddog-gov.com/v1/metrics", True),
        ("http://localhost:4318/v1/metrics", False),
        ("https://otlp.example.com/v1/metrics", False),
        ("https://otlp.datadoghq.com.example.com/v1/metrics", False),
    ],
)
def test_an_explicitly_configured_intake_gets_the_datadog_binding(url, datadog):
    assert _usage_metrics_writer._is_datadog_intake(url) is datadog


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


def test_a_partial_success_is_read_from_the_response():
    metrics_service_pb2 = pytest.importorskip("opentelemetry.proto.collector.metrics.v1.metrics_service_pb2")
    response = metrics_service_pb2.ExportMetricsServiceResponse()
    response.partial_success.rejected_data_points = 3
    response.partial_success.error_message = "unit not allowed"
    assert _usage_metrics_writer._partial_success(response.SerializeToString()) == (3, "unit not allowed")
    assert (
        _usage_metrics_writer._partial_success(metrics_service_pb2.ExportMetricsServiceResponse().SerializeToString())
        is None
    )
    assert _usage_metrics_writer._partial_success(b"") is None
    assert _usage_metrics_writer._partial_success(b"\x0a\x05\x08") is None
    assert _usage_metrics_writer._partial_success(b"not protobuf") is None


def test_a_partial_success_is_reported_and_not_sent_again(otlp_endpoint, monkeypatch):
    metrics_service_pb2 = pytest.importorskip("opentelemetry.proto.collector.metrics.v1.metrics_service_pb2")
    response = metrics_service_pb2.ExportMetricsServiceResponse()
    response.partial_success.rejected_data_points = 2
    response.partial_success.error_message = "unit not allowed"
    Collector.response_body = response.SerializeToString()
    warnings = []
    monkeypatch.setattr(_usage_metrics_writer.log, "warning", lambda *args: warnings.append(args))
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    writer.flush()
    assert len(Collector.requests) == 1
    assert [w[2:] for w in warnings] == [(2, "unit not allowed")]


def test_a_periodic_flush_starts_no_export_once_close_began(otlp_endpoint):
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer._closing = True
    writer.flush()
    assert Collector.requests == []
    writer.on_shutdown(time.monotonic() + 5)
    assert len(Collector.requests) == 1


def test_periodic_exports_continue_after_a_tracer_shutdown(otlp_endpoint):
    # An application can shut the global tracer down, which runs on_shutdown, and keep serving.
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.on_shutdown(time.monotonic() + 5)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    assert len(Collector.requests) == 1


def test_retry_after_reads_an_http_date():
    later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=120)
    assert 100 < _usage_metrics_writer._retry_after(email.utils.format_datetime(later, usegmt=True)) <= 120
    assert _usage_metrics_writer._retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0
    assert _usage_metrics_writer._retry_after("soon") is None


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


def test_the_flushes_of_one_exit_share_its_deadline(otlp_endpoint, monkeypatch):
    # The proxy's shutdown flushes first, then the exit signal or atexit: together they take no longer than the
    # shutdown time.
    writer = UsageMetricsWriter("otlp", interval=60)
    flushes = []
    monkeypatch.setattr(writer, "flush", lambda retry=True, deadline=None: flushes.append((retry, deadline)))
    writer.on_exit()
    writer.on_shutdown()
    [(retry, deadline), later] = flushes
    assert retry is False and deadline is not None and later == (False, deadline)
    assert writer._stopping.is_set()


def test_on_shutdown_tries_pending_exports_once_without_waiting(otlp_endpoint, sleeps):
    Collector.status = 503
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    sent, waited = len(Collector.requests), len(sleeps)
    writer.record(PROVIDER_ATTEMPT, *observation(user="u2"))
    writer.on_shutdown()
    # Each export is tried once, the kept one first; none waits. The application may keep serving after a tracer
    # shutdown, so both stay queued for a later flush.
    assert len(Collector.requests) == sent + 2
    assert len(sleeps) == waited
    assert len(writer._pending) == 2
    Collector.status = 200
    writer.flush()
    assert not writer._pending


def test_close_discards_exports_that_still_failed(otlp_endpoint, sleeps):
    Collector.status = 503
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.start()
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    writer.close()
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
    # Out of time: what is left is not sent, and stays queued for a later flush unless the writer is closing.
    assert len(Collector.requests) == sent
    assert len(writer._pending) == 1


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
    # Nor does the last flush wait out a periodic flush whose request the collector does not answer. A new writer:
    # once shutting down, a writer's periodic flushes start no request.
    writer = UsageMetricsWriter("otlp", interval=60)
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


def test_a_dogstatsd_send_in_progress_stops_at_the_close_deadline(monkeypatch):
    # A periodic flush, given no deadline, is sending when close() starts: it stops at close()'s deadline and does
    # not open the socket again afterwards.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    monkeypatch.setattr(agent_config, "dogstatsd_url", f"udp://127.0.0.1:{sock.getsockname()[1]}")
    receiver = Receiver(sock)
    writer = UsageMetricsWriter("dogstatsd", interval=60)
    sender = writer._dogstatsd
    sender._max_packet = 1
    connect = sender._connect

    def connect_then_close_starts():
        connected = connect()
        sender.stop_by(time.monotonic() - 1)
        return connected

    monkeypatch.setattr(sender, "_connect", connect_then_close_starts)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    assert receiver.stop() == []
    assert sender._socket is None


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


def test_dogstatsd_rechecks_the_deadline_after_resolving_the_host(monkeypatch):
    # Name resolution is not bounded by the socket timeout: a slow one must not lead to a send after the deadline.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

    def slow_getaddrinfo(host, *args, **kwargs):
        time.sleep(0.2)
        return [(socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("127.0.0.1", port))]

    monkeypatch.setattr(socket, "getaddrinfo", slow_getaddrinfo)
    monkeypatch.setattr(agent_config, "dogstatsd_url", f"udp://dogstatsd.test:{port}")
    receiver = Receiver(sock)
    sender = _usage_metrics_writer._DogStatsdSender()
    sender.send(["a:1|c"], deadline=time.monotonic() + 0.05)
    assert receiver.stop() == []
    assert sender._socket is None


class FullSocket:
    """A datagram socket whose buffer stays full, as macOS reports at once instead of blocking."""

    def __init__(self):
        self.attempts = 0
        self.closed = False

    def send(self, data):
        self.attempts += 1
        raise OSError(errno.ENOBUFS, "No buffer space available")

    def close(self):
        self.closed = True


def test_dogstatsd_retries_stop_at_the_shutdown_deadline(monkeypatch):
    monkeypatch.setattr(agent_config, "dogstatsd_url", "unix:///nonexistent/dsd.sock")
    sender = _usage_metrics_writer._DogStatsdSender()
    sock = FullSocket()
    sender._socket = sock
    sender.send(["a:1|c"], deadline=time.monotonic() + 0.02)
    # Each retry waits a few milliseconds; the deadline ends them long before the last attempt.
    assert 1 <= sock.attempts < _usage_metrics_writer._SEND_ATTEMPTS


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


@pytest.fixture
def no_compression_settings(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_METRICS_COMPRESSION", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_COMPRESSION", raising=False)
    monkeypatch.setattr(_usage_metrics_writer, "_compression_warned", False)


def test_otlp_exports_are_gzipped_by_default(otlp_endpoint, no_compression_settings):
    metrics_service_pb2 = pytest.importorskip("opentelemetry.proto.collector.metrics.v1.metrics_service_pb2")
    writer = UsageMetricsWriter("otlp", interval=60)
    for i in range(50):
        writer.record(PROVIDER_ATTEMPT, *observation(user=f"user-{i:03d}"))
    writer.flush()
    [(_, headers, body)] = Collector.requests
    assert headers["content-encoding"] == "gzip"
    assert headers["content-type"] == "application/x-protobuf"
    # The collector decoded the body it received; the series repeat their names and attributes, which compress well.
    request = metrics_service_pb2.ExportMetricsServiceRequest()
    request.ParseFromString(body)
    assert request.resource_metrics
    assert Collector.sizes[0] * 5 < len(body)


@pytest.mark.parametrize(
    "variables, gzipped",
    [
        ({"OTEL_EXPORTER_OTLP_COMPRESSION": "none"}, False),
        ({"OTEL_EXPORTER_OTLP_METRICS_COMPRESSION": "NONE"}, False),
        ({"OTEL_EXPORTER_OTLP_COMPRESSION": "none", "OTEL_EXPORTER_OTLP_METRICS_COMPRESSION": "gzip"}, True),
        ({"OTEL_EXPORTER_OTLP_COMPRESSION": "gzip", "OTEL_EXPORTER_OTLP_METRICS_COMPRESSION": "none"}, False),
    ],
)
def test_otlp_compression_follows_the_settings(otlp_endpoint, no_compression_settings, monkeypatch, variables, gzipped):
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    writer = UsageMetricsWriter("otlp", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    [(_, headers, body)] = Collector.requests
    assert ("content-encoding" in headers) is gzipped
    assert b"gen_ai.client.inference.duration" in body
    assert (Collector.sizes[0] < len(body)) is gzipped


@pytest.mark.parametrize("source_name", ("LOCAL_CONFIG", "FLEET_CONFIG"))
def test_otlp_compression_comes_from_stable_configuration(no_compression_settings, monkeypatch, source_name):
    monkeypatch.setitem(getattr(settings_core, source_name), "OTEL_EXPORTER_OTLP_METRICS_COMPRESSION", "none")
    assert _usage_metrics_writer._compression() is False


def test_an_unknown_otlp_compression_warns_once_and_uses_gzip(no_compression_settings, monkeypatch):
    warnings = []
    monkeypatch.setattr(_usage_metrics_writer.log, "warning", lambda *args: warnings.append(args))
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_COMPRESSION", "zstd")
    assert _usage_metrics_writer._compression() is True
    assert _usage_metrics_writer._compression() is True
    assert [args[1] for args in warnings] == ["zstd"]


def test_pending_exports_are_bounded_by_size(otlp_endpoint, no_compression_settings, monkeypatch, sleeps):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_COMPRESSION", "none")
    Collector.status = 503
    writer = UsageMetricsWriter("otlp", interval=60)
    users = ("user-one", "user-two", "user-three", "user-four")
    for user in users[:2]:
        writer.record(PROVIDER_ATTEMPT, *observation(user=user))
        writer.flush()
    # Room for the two exports pending and one more, not two more.
    size = max(len(body) for body in writer._pending)
    monkeypatch.setattr(_usage_metrics_writer, "_MAX_PENDING_BYTES", 3 * size + size // 2)
    for user in users[2:]:
        writer.record(PROVIDER_ATTEMPT, *observation(user=user))
        writer.flush()
    assert len(writer._pending) == 3
    Collector.status = 200
    sent = len(Collector.requests)
    writer.flush()
    bodies = [body for _, _, body in Collector.requests[sent:]]
    # The oldest export was dropped first.
    assert [user for body in bodies for user in users if user.encode() in body] == list(users[1:])


def test_an_export_larger_than_the_size_bound_is_still_kept(otlp_endpoint, monkeypatch, sleeps):
    monkeypatch.setattr(_usage_metrics_writer, "_MAX_PENDING_BYTES", 1)
    Collector.status = 503
    writer = UsageMetricsWriter("otlp", interval=60)
    for user in ("user-one", "user-two"):
        writer.record(PROVIDER_ATTEMPT, *observation(user=user))
        writer.flush()
    [body] = writer._pending
    assert b"user-two" in gzip.decompress(body)


def test_dogstatsd_send_failures_warn(monkeypatch):
    # A UDP port nobody listens on: the first send succeeds, a later one is refused once the ICMP error came back.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    warnings = []
    monkeypatch.setattr(_usage_metrics_writer.log, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(agent_config, "dogstatsd_url", f"udp://127.0.0.1:{port}")
    writer = UsageMetricsWriter("dogstatsd", interval=60)
    for _ in range(5):
        writer.record(PROVIDER_ATTEMPT, *observation())
        writer.flush()
        if warnings:
            break
        time.sleep(0.05)
    assert warnings, "no warning for a refused DogStatsD port"
    assert warnings[0][1] == f"127.0.0.1:{port}"
    assert warnings[0][2] == "ECONNREFUSED"


def test_dogstatsd_warns_when_the_socket_is_missing(monkeypatch):
    warnings = []
    monkeypatch.setattr(_usage_metrics_writer.log, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(agent_config, "dogstatsd_url", "unix:///nonexistent/dsd.sock")
    writer = UsageMetricsWriter("dogstatsd", interval=60)
    writer.record(PROVIDER_ATTEMPT, *observation())
    writer.flush()
    [args] = warnings
    assert args[1] == "/nonexistent/dsd.sock"
    assert args[2] == "ENOENT"


def test_dogstatsd_warns_when_the_socket_stays_full(monkeypatch):
    warnings = []
    monkeypatch.setattr(_usage_metrics_writer.log, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(_usage_metrics_writer.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(agent_config, "dogstatsd_url", "unix:///var/run/dsd.sock")
    sender = _usage_metrics_writer._DogStatsdSender()
    sock = FullSocket()
    sender._socket = sock
    sender.send(["a:1|c", "b:1|c"])
    assert sock.attempts == _usage_metrics_writer._SEND_ATTEMPTS
    assert warnings == [(warnings[0][0], "/var/run/dsd.sock", "ENOBUFS", 2)]


def _series_points(body):
    """Each data point of an export by its series, as its serialized protobuf: a series is its resource, scope,
    metric and attributes.
    """
    metrics_service_pb2 = pytest.importorskip("opentelemetry.proto.collector.metrics.v1.metrics_service_pb2")
    request = metrics_service_pb2.ExportMetricsServiceRequest()
    request.ParseFromString(body)
    points = {}
    for resource_metrics in request.resource_metrics:
        resource = resource_metrics.resource.SerializeToString()
        for scope_metrics in resource_metrics.scope_metrics:
            scope = scope_metrics.scope.SerializeToString()
            for metric in scope_metrics.metrics:
                kind = metric.WhichOneof("data")
                for point in getattr(metric, kind).data_points:
                    attributes = tuple(sorted((a.key, a.value.SerializeToString()) for a in point.attributes))
                    key = (resource, scope, metric.name, metric.unit, kind, attributes)
                    assert key not in points, key
                    points[key] = point.SerializeToString()
    return points


def _usage_with(series, repeat=2):
    """Two UsageMetrics that recorded the same observations: each series more than once, so that counters add up and
    histograms have several samples.
    """
    usages = [ai_usage.UsageMetrics(), ai_usage.UsageMetrics()]
    for i in range(series):
        for r in range(repeat):
            obs, attributes = observation(user=f"user-{i:05d}", input_tokens=10 + r)
            obs["duration_seconds"] = 0.5 + r + i % 7
            obs["cost_usd"] = 1e-6 * (r + 1)
            for usage in usages:
                usage.record(PROVIDER_ATTEMPT, obs, dict(attributes, **{"service.name": f"svc-{i % 3}"}))
    return usages


@pytest.mark.parametrize("datadog", [False, True])
def test_a_large_otlp_export_splits_by_series(datadog):
    whole_usage, parts_usage = _usage_with(10_000)
    whole = whole_usage.take_otlp("scope", "1", 1, 2, datadog=datadog)
    max_bytes = _usage_metrics_writer._MAX_REQUEST_BYTES
    parts = parts_usage.take_otlp_parts("scope", "1", 1, 2, max_bytes, datadog=datadog)
    assert len(whole) > 4 * max_bytes
    assert len(parts) > 4
    assert all(len(part) <= max_bytes for part in parts), [len(part) for part in parts]
    # The parts together hold every point of the export, exactly as it encodes them: each series, its histogram
    # samples included, is in one part only.
    union = {}
    for part in parts:
        for key, point in _series_points(part).items():
            assert key not in union, key
            union[key] = point
    assert union == _series_points(whole)
    assert parts_usage.take_otlp_parts("scope", "1", 2, 3, max_bytes) == []


def test_an_otlp_export_that_fits_is_one_identical_request():
    whole_usage, parts_usage = _usage_with(50)
    whole = whole_usage.take_otlp("scope", "1", 1, 2)
    assert parts_usage.take_otlp_parts("scope", "1", 1, 2, len(whole)) == [whole]


def test_a_series_is_never_split_across_requests():
    whole_usage, parts_usage = _usage_with(5, repeat=3)
    whole = _series_points(whole_usage.take_otlp("scope", "1", 1, 2))
    # Too small for any series: one request per series, each with all its samples.
    parts = parts_usage.take_otlp_parts("scope", "1", 1, 2, 1)
    assert len(parts) == len(whole)
    union = {}
    for part in parts:
        [(key, point)] = _series_points(part).items()
        union[key] = point
    assert union == whole


def test_the_requests_of_a_flush_wait_in_order_for_a_failing_endpoint(otlp_endpoint, monkeypatch, sleeps):
    monkeypatch.setattr(_usage_metrics_writer, "_MAX_REQUEST_BYTES", 4000)
    writer = UsageMetricsWriter("otlp", interval=60)
    users = [f"user-{i:03d}" for i in range(60)]
    for user in users:
        writer.record(PROVIDER_ATTEMPT, *observation(user=user))
    Collector.status = 503
    writer.flush()
    queued = [gzip.decompress(body) for body in writer._pending]
    parts = len(queued)
    assert parts > 5
    # Only the first request was tried (and retried); the others wait behind it.
    assert len(Collector.requests) == 1 + len(_usage_metrics_writer._RETRY_DELAYS_SECONDS)
    # The endpoint takes two requests, then fails again: the rest keep waiting, in order.
    Collector.statuses = [200, 200]
    writer.flush()
    assert len(writer._pending) == parts - 2
    Collector.status = 200
    writer.flush()
    assert not writer._pending
    # A retried request is sent again as it was: in order of first sending, the requests are the queued ones, in
    # their order, and hold every point of the flush once.
    delivered = list(dict.fromkeys(body for _, _, body in Collector.requests))
    assert delivered == queued
    keys = [key for body in delivered for key in _series_points(body)]
    assert len(keys) == len(set(keys))
    assert all(any(user.encode() in body for body in delivered) for user in users)


def test_pending_requests_of_one_flush_are_kept_while_they_fit(otlp_endpoint, monkeypatch, sleeps):
    monkeypatch.setattr(_usage_metrics_writer, "_MAX_REQUEST_BYTES", 4000)
    Collector.status = 503
    writer = UsageMetricsWriter("otlp", interval=60)
    for i in range(60):
        writer.record(PROVIDER_ATTEMPT, *observation(user=f"old-{i:03d}"))
    writer.flush()
    first = list(writer._pending)
    # Room for the newer flush's requests and part of the older one's: the older requests go first.
    monkeypatch.setattr(_usage_metrics_writer, "_MAX_PENDING_BYTES", sum(map(len, first)) + len(first[0]) // 2)
    for i in range(60):
        writer.record(PROVIDER_ATTEMPT, *observation(user=f"new-{i:03d}"))
    writer.flush()
    pending = list(writer._pending)
    newer = pending[-len(first) :]
    assert all(b"new-" in gzip.decompress(body) for body in newer)
    assert sum(b"old-" in gzip.decompress(body) for body in pending) < len(first)
    assert sum(map(len, pending)) <= _usage_metrics_writer._MAX_PENDING_BYTES
