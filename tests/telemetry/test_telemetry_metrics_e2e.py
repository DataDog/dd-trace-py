from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from ddtrace.internal.compat import PYTHON_VERSION_INFO
from ddtrace.internal.utils.retry import RetryError
from tests.utils import _build_env
from tests.webclient import Client


FILE_PATH = Path(__file__).resolve().parent


@contextmanager
def gunicorn_server(telemetry_metrics_enabled="true", token=None):
    cmd = ["ddtrace-run", "gunicorn", "-w", "1", "-b", "0.0.0.0:8000", "tests.telemetry.app:app"]
    env = _build_env(file_path=FILE_PATH)
    env["_DD_TRACE_WRITER_ADDITIONAL_HEADERS"] = f"X-Datadog-Test-Session-Token:{token}"
    env["DD_TRACE_AGENT_URL"] = os.environ.get("DD_TRACE_AGENT_URL", "")
    env["DD_TRACE_DEBUG"] = "true"
    # do not patch flask because we will end up with confusing metrics
    # now that we generate metrics for spans
    env["DD_PATCH_MODULES"] = "flask:false"
    server_process = subprocess.Popen(
        cmd,
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
        close_fds=True,
        preexec_fn=os.setsid,
    )
    try:
        client = Client("http://0.0.0.0:8000")
        try:
            print("Waiting for server to start")
            client.wait(max_tries=100, delay=0.1)
            print("Server started")
        except RetryError:
            raise AssertionError(
                "Server failed to start, see stdout and stderr logs"
                "\n=== Captured STDOUT ===\n%s=== End of captured STDOUT ==="
                "\n=== Captured STDERR ===\n%s=== End of captured STDERR ==="
                % (server_process.stdout, server_process.stderr)
            )

        yield server_process, client
        try:
            client.get_ignored("/shutdown")
        except Exception:
            raise AssertionError(
                "\n=== Captured STDOUT ===\n%s=== End of captured STDOUT ==="
                "\n=== Captured STDERR ===\n%s=== End of captured STDERR ==="
                % (server_process.stdout, server_process.stderr)
            )
    finally:
        server_process.terminate()
        server_process.wait()


def parse_payload(data):
    return json.loads(data)


@pytest.mark.skipif(PYTHON_VERSION_INFO >= (3, 14), reason="Gunicorn doesn't yet work with Python 3.14")
def test_telemetry_metrics_enabled_on_gunicorn_child_process(test_agent_session):
    # Must be the fixture's token, not a hand-written one: the telemetry worker now propagates
    # the session token, so a mismatched token files the child's payloads under a session this
    # test never queries.
    token = test_agent_session.token
    with gunicorn_server(telemetry_metrics_enabled="true", token=token) as context:
        _, gunicorn_client = context

        gunicorn_client.get("/count_metric")
        gunicorn_client.get("/count_metric")
        response = gunicorn_client.get("/count_metric")
        assert response.status_code == 200
        gunicorn_client.get("/count_metric")
        response = gunicorn_client.get("/count_metric")
        assert response.status_code == 200

    # Ensure /count_metric was called 5 times (these counts could be sent in different payloads)
    metrics = test_agent_session.get_metrics("test_metric")
    count = 0
    for metric in metrics:
        count += metric["points"][0][1]
    assert count == 5


def test_span_creation_and_finished_metrics_datadog(test_agent_session, ddtrace_run_python_code_in_subprocess):
    code = """
from ddtrace.trace import tracer
for _ in range(10):
    with tracer.trace('span1') as span:
        span.set_tag("component", "custom")
        pass
"""
    env = os.environ.copy()
    # Keep the subprocess writer non-agentless (a stray DD_API_KEY would route to intake).
    env.pop("DD_API_KEY", None)
    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr
    metrics_sc = test_agent_session.get_metrics("spans_created")

    assert len(metrics_sc) == 1
    assert metrics_sc[0]["metric"] == "spans_created"
    assert metrics_sc[0]["tags"] == ["integration_name:datadog"]
    assert metrics_sc[0]["points"][0][1] == 10

    metrics_sf = test_agent_session.get_metrics("spans_finished")
    assert len(metrics_sf) == 1
    assert metrics_sf[0]["metric"] == "spans_finished"
    assert metrics_sf[0]["tags"] == ["integration_name:custom"]
    assert metrics_sf[0]["points"][0][1] == 10


def test_span_creation_and_finished_metrics_otel(test_agent_session, ddtrace_run_python_code_in_subprocess):
    code = """
import opentelemetry.trace

ot = opentelemetry.trace.get_tracer(__name__)
for _ in range(9):
    with ot.start_span('span'):
        pass
"""
    env = os.environ.copy()
    env["DD_TRACE_OTEL_ENABLED"] = "true"
    # Keep the subprocess writer non-agentless (a stray DD_API_KEY would route to intake).
    env.pop("DD_API_KEY", None)
    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr

    metrics_sc = test_agent_session.get_metrics("spans_created")
    assert len(metrics_sc) == 1
    assert metrics_sc[0]["metric"] == "spans_created"
    assert metrics_sc[0]["tags"] == ["integration_name:otel"]
    assert metrics_sc[0]["points"][0][1] == 9

    metrics_sf = test_agent_session.get_metrics("spans_finished")
    assert len(metrics_sf) == 1
    assert metrics_sf[0]["metric"] == "spans_finished"
    assert metrics_sf[0]["tags"] == ["integration_name:otel"]
    assert metrics_sf[0]["points"][0][1] == 9


def test_metric_burst_during_slow_telemetry_request():
    """A full metric buffer drops points silently and still lets the application export a trace."""
    app_ready = threading.Event()
    telemetry_received = threading.Event()
    resume = threading.Event()
    trace_before_resume = threading.Event()
    metrics = []

    class Agent(BaseHTTPRequestHandler):
        def handle_request(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            status = 200
            if self.path == "/ready":
                app_ready.set()
                if not telemetry_received.wait(10):
                    self.send_error(504, "the worker did not contact the Agent")
                    return
            elif self.path == "/resume":
                resume.set()
            elif self.path.endswith("/traces"):
                if telemetry_received.is_set() and not resume.is_set():
                    trace_before_resume.set()
            elif self.path.endswith("/apmtelemetry"):
                request = json.loads(body)
                events = request["payload"] if request["request_type"] == "message-batch" else [request]
                for event in events:
                    if event["request_type"] == "generate-metrics":
                        metrics.extend(event["payload"]["series"])
                if app_ready.is_set() and not telemetry_received.is_set():
                    telemetry_received.set()
                    resume.wait()
                    status = 202
            self.send_response(status)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        do_GET = do_POST = do_PUT = handle_request

        def log_message(self, *args):
            pass

    code = """
from http.client import HTTPConnection
import os
import sys

from ddtrace.internal.native._native import logger
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.trace import tracer

def signal_agent(path):
    conn = HTTPConnection("127.0.0.1", int(sys.argv[1]), timeout=15)
    conn.request("GET", path)
    assert conn.getresponse().status == 200
    conn.close()

assert telemetry_writer.enable()
tracer._span_aggregator.writer._sync_mode = True
signal_agent("/ready")
logger.set_log_level("debug")
for _ in range(2500):
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, "burst", 1)
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, "burst", 1, (("kind", "tagged"),))

logger.set_log_level("error")
with tracer.trace("test"):
    pass

logger.set_log_level("debug")
signal_agent("/resume")
telemetry_writer.periodic(force_flush=True)
logger.set_log_level("error")
telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, "after_burst", 1)
telemetry_writer.periodic(force_flush=True)
os._exit(0)
"""
    with ThreadingHTTPServer(("127.0.0.1", 0), Agent) as server:
        port = server.server_port
        # Use the local Agent even when the parent configures agentless or OTLP export.
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DD_", "_DD_", "DATADOG_", "OTEL_"))}
        env.pop("RUST_LOG", None)
        env.update(
            DD_TRACE_AGENT_URL=f"http://127.0.0.1:{port}",
            DD_TELEMETRY_HEARTBEAT_INTERVAL="1",
            _DD_NATIVE_LOGGING_BACKEND="stderr",
            _DD_NATIVE_LOGGING_LOG_LEVEL="error",
        )
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with subprocess.Popen(
                [sys.executable, "-c", code, str(port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=env,
            ) as process:
                try:
                    # Leave stderr unread so per-point diagnostics would fill the pipe and block the caller.
                    if not resume.wait(30):
                        process.kill()
                    _, stderr = process.communicate(timeout=15)
                finally:
                    process.kill()
        finally:
            resume.set()
            server.shutdown()
            thread.join()

    assert process.returncode == 0, stderr
    assert trace_before_resume.is_set(), "the synchronous exporter did not send a trace before the Agent resumed"
    assert any(
        b"Successfully sent telemetry payload" in line and b"response.status=202" in line
        for line in stderr.splitlines()
    ), ("the telemetry request timed out before the synchronous export finished", stderr)
    assert b"telemetry: dropped metric point" not in stderr
    assert b"Error sending telemetry" not in stderr
    for tags in ([], ["kind:tagged"]):
        burst_count = sum(
            value
            for series in metrics
            if series["metric"] == "burst" and series["tags"] == tags
            for _, value in series["points"]
        )
        assert 0 < burst_count < 2500, (tags, burst_count)
    recovered = [value for series in metrics if series["metric"] == "after_burst" for _, value in series["points"]]
    assert recovered == [1]
