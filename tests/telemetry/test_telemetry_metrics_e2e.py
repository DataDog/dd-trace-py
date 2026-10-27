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
def agent_that_never_answers_telemetry(trace_request_received=None):
    """Serve a stand-in agent that answers every request at once, except telemetry requests.

    A telemetry request gets no response until the context exits. This is how an overloaded
    agent behaves: it accepts the connection and then answers late or never.
    """
    release = threading.Event()
    request_received = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def _serve(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if "telemetry" in self.path:
                request_received.set()
                release.wait()
            elif self.path.endswith("/traces") and trace_request_received is not None:
                trace_request_received.set()
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
        yield "http://127.0.0.1:%d" % server.server_address[1], request_received
    finally:
        release.set()
        server.shutdown()
        server.server_close()


def _local_agent_telemetry_env():
    env = os.environ.copy()
    # Keep the subprocess on the local agent, even if the parent uses agentless mode.
    for name in (
        "DD_API_KEY",
        "DD_AGENTLESS_ENABLED",
        "_DD_APM_TRACING_AGENTLESS_ENABLED",
        "DD_CIVISIBILITY_AGENTLESS_ENABLED",
        "DD_LLMOBS_AGENTLESS_ENABLED",
    ):
        env.pop(name, None)
    env["DD_INSTRUMENTATION_TELEMETRY_ENABLED"] = "true"
    env["DD_TELEMETRY_HEARTBEAT_INTERVAL"] = "1"
    # Unrelated Python debug logs can block when a test leaves stderr unread.
    env["DD_TRACE_DEBUG"] = "false"
    return env


def test_metric_points_do_not_wait_for_a_slow_agent():
    """Recording a telemetry metric must never wait for the telemetry worker.

    The worker does not read its metric buffer while it waits for the response to a telemetry
    request, and a slow agent keeps it there for the whole request timeout. A recording thread
    that waited for free space in a full buffer would stop with the GIL held, and every other
    thread of the application would stop with it.
    """
    code = """
import os
import time

from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE

assert telemetry_writer.enable()
input()
start = time.monotonic()
for _ in range(5000):
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, "test_metric", 1)
print(time.monotonic() - start, flush=True)
os._exit(0)
"""
    env = _local_agent_telemetry_env()
    with agent_that_never_answers_telemetry() as (agent_url, request_received):
        env["DD_TRACE_AGENT_URL"] = agent_url
        process = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            assert request_received.wait(15), "the telemetry worker did not contact the agent"
            stdout, stderr = process.communicate(input=b"\n", timeout=15)
            assert process.returncode == 0, stderr
            elapsed = float(stdout.decode().strip().splitlines()[-1])
            assert elapsed < 2.0, "recording 5000 telemetry metric points took %.1f seconds" % elapsed
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


def test_metric_points_do_not_block_on_full_debug_log():
    """Metric drops must not block on a full debug log pipe."""
    code = """
import os
import time
from ddtrace.internal.native._native import logger
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE

telemetry_writer.enable()
input()
logger.set_log_level("debug")
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    for _ in range(2500):
        telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, "test_metric", 1)
        telemetry_writer.add_count_metric(
            TELEMETRY_NAMESPACE.TRACERS, "test_metric", 1, tags=(("integration_name", "test"),)
        )
    time.sleep(0.05)
os._exit(0)
"""
    env = _local_agent_telemetry_env()
    env["_DD_NATIVE_LOGGING_BACKEND"] = "stderr"
    env["_DD_NATIVE_LOGGING_LOG_LEVEL"] = "warning"
    with agent_that_never_answers_telemetry() as (agent_url, request_received):
        env["DD_TRACE_AGENT_URL"] = agent_url
        process = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            assert request_received.wait(15), "the telemetry worker did not contact the agent"
            process.stdin.write(b"\n")
            process.stdin.flush()
            try:
                status = process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                pytest.fail("recording telemetry metric points blocked while stderr was not being read")
            assert status == 0, process.stderr.read().decode(errors="replace")
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


@pytest.mark.skipif(sys.platform == "win32", reason="Nonblocking pipe writes are not supported on all Python versions")
def test_trace_export_does_not_block_on_full_telemetry_log():
    """A dropped telemetry point must not block synchronous trace export on stderr."""
    code = """
import os
import sys

from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.trace import tracer

assert telemetry_writer.enable()
tracer._span_aggregator.writer._sync_mode = True
input()
for _ in range(5000):
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, "test_metric", 1)

fd = sys.stderr.fileno()
os.set_blocking(fd, False)
try:
    while True:
        os.write(fd, b"x" * 4096)
except BlockingIOError:
    pass
finally:
    os.set_blocking(fd, True)

with tracer.trace("test"):
    pass
os._exit(0)
"""
    env = _local_agent_telemetry_env()
    env["DD_TRACE_ENABLED"] = "true"
    env["_DD_NATIVE_LOGGING_BACKEND"] = "stderr"
    env["_DD_NATIVE_LOGGING_LOG_LEVEL"] = "error"
    trace_request_received = threading.Event()
    with agent_that_never_answers_telemetry(trace_request_received) as (agent_url, request_received):
        env["DD_TRACE_AGENT_URL"] = agent_url
        process = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            assert request_received.wait(15), "the telemetry worker did not contact the agent"
            process.stdin.write(b"\n")
            process.stdin.flush()
            try:
                status = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _, stderr = process.communicate(timeout=5)
                pytest.fail("synchronous trace export blocked while stderr was full: %r" % stderr[-1000:])
            assert status == 0
            assert trace_request_received.is_set(), "the trace exporter did not contact the agent"
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
