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


@contextmanager
def agent_that_never_answers_telemetry():
    """Serve a stand-in agent that answers every request at once, except telemetry requests.

    A telemetry request gets no response until the context exits. This is how an overloaded
    agent behaves: it accepts the connection and then answers late or never.
    """
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def _serve(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if "telemetry" in self.path:
                release.wait()
            body = b"{}"
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                # The client gave up on a request that got no response in time.
                pass

        do_GET = do_POST = do_PUT = _serve

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % server.server_address[1]
    finally:
        release.set()
        server.shutdown()
        server.server_close()


def test_metric_points_do_not_wait_for_a_slow_agent(ddtrace_run_python_code_in_subprocess):
    """Recording a telemetry metric must never wait for the telemetry worker.

    The worker does not read its metric buffer while it waits for the response to a telemetry
    request, and a slow agent keeps it there for the whole request timeout. A recording thread
    that waited for free space in a full buffer would stop with the GIL held, and every other
    thread of the application would stop with it.
    """
    code = """
import time

from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE

longest = 0.0
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    start = time.monotonic()
    # More points than the metric buffer of the worker holds.
    for _ in range(5000):
        telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, "test_metric", 1)
    longest = max(longest, time.monotonic() - start)
    time.sleep(0.05)
print(longest)
"""
    env = os.environ.copy()
    # Keep the subprocess writer non-agentless (a stray DD_API_KEY would route to intake).
    env.pop("DD_API_KEY", None)
    env["DD_TELEMETRY_HEARTBEAT_INTERVAL"] = "1"
    with agent_that_never_answers_telemetry() as agent_url:
        env["DD_TRACE_AGENT_URL"] = agent_url
        stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr
    longest = float(stdout.decode().strip().splitlines()[-1])
    assert longest < 1.0, "recording 5000 telemetry metric points took %.1f seconds" % longest
