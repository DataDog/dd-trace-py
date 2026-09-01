from http.client import HTTPConnection
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import os
import platform
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

import msgpack
import pytest


if sys.platform == "win32":
    pytestmark = pytest.mark.skip


UWSGI_APP = os.path.join(os.path.dirname(__file__), "uwsgi-prefork-app.py")
INJECTION_SOURCES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "lib-injection", "sources"))
DDTRACE_PACKAGE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ddtrace"))
SPAN_NAME = b"test.uwsgi.prefork"
TIMEOUT = 10


def _trace_ids_for_span(payload, span_name):
    decoded = msgpack.unpackb(payload, raw=True, strict_map_key=False)
    if decoded and decoded[0] and isinstance(decoded[0][0], bytes):
        string_table, traces = decoded
        return {span[3] for trace in traces for span in trace if string_table[span[1]] == span_name}
    return {span[b"trace_id"] for trace in decoded for span in trace if span[b"name"] == span_name}


@pytest.fixture
def trace_agent():
    trace_available = threading.Condition()
    trace_ids = set()

    class Handler(BaseHTTPRequestHandler):
        def _respond(self, body=b"{}"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._respond(b'{"endpoints":["/v0.5/traces"]}')

        def do_PUT(self):
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length)
            if self.path.endswith("/traces"):
                with trace_available:
                    trace_ids.update(_trace_ids_for_span(body, SPAN_NAME))
                    trace_available.notify_all()
            self._respond(b'{"rate_by_service":{}}')

        do_POST = do_PUT

        def log_message(self, format_string, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    host, port = server.server_address
    yield "http://%s:%d" % (host, port), trace_available, trace_ids

    server.shutdown()
    server.server_close()
    thread.join(timeout=TIMEOUT)


class _UnixHTTPConnection(HTTPConnection):
    def __init__(self, socket_path, timeout):
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._socket_path)


def _terminate(proc):
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            return proc.communicate(timeout=TIMEOUT)[0]
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
    return proc.communicate()[0]


def _request_when_ready(proc, socket_path):
    deadline = time.monotonic() + TIMEOUT
    last_error = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            output = proc.communicate()[0]
            raise AssertionError("uWSGI exited before serving a request: %r" % output)
        connection = _UnixHTTPConnection(socket_path, timeout=0.5)
        try:
            connection.request("GET", "/")
            return connection.getresponse().read()
        except (HTTPException, OSError) as error:
            last_error = error
            time.sleep(0.1)
        finally:
            connection.close()
    output = _terminate(proc)
    raise AssertionError("uWSGI did not serve a request (%r): %r" % (last_error, output))


def _prepare_injection_sources(tmp_path):
    sources = tmp_path / "ssi"
    shutil.copytree(INJECTION_SOURCES, sources)
    architecture = "aarch64" if platform.machine() in ("aarch64", "arm64") else "x86_64"
    package_root = (
        sources
        / "ddtrace_pkgs"
        / ("site-packages-ddtrace-py%d.%d-manylinux2014-%s" % (*sys.version_info[:2], architecture))
    )
    package_root.mkdir(parents=True)
    (package_root / "ddtrace").symlink_to(DDTRACE_PACKAGE, target_is_directory=True)
    (sources / "version").write_text("test")
    return sources


def _prepare_telemetry_forwarder(tmp_path):
    telemetry_pids = tmp_path / "ssi-pids"
    forwarder = tmp_path / "telemetry-forwarder"
    forwarder_code = (
        "import json, os, sys; "
        "payload = json.load(sys.stdin); "
        "fd = os.open(os.environ['DD_TEST_SSI_PIDS'], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600); "
        "metadata = payload['metadata']; "
        "os.write(fd, ('%s:%s\\n' % (metadata['pid'], metadata['result_class'])).encode()); "
        "os.close(fd)"
    )
    forwarder.write_text(
        "#!/bin/sh\nPYTHONPATH= exec %s -c %s\n" % (shlex.quote(sys.executable), shlex.quote(forwarder_code))
    )
    forwarder.chmod(0o755)
    return forwarder, telemetry_pids


@pytest.mark.parametrize(
    "payload",
    [
        msgpack.packb([[{b"name": b"other", b"trace_id": 1}]]),
        msgpack.packb([[b"", b"other"], [[[0, 1, 0, 1]]]]),
    ],
    ids=["v04", "v05"],
)
def test_trace_ids_for_span_ignores_other_spans(payload):
    assert not _trace_ids_for_span(payload, SPAN_NAME)


@pytest.mark.parametrize(
    "payload",
    [
        msgpack.packb([[{b"name": SPAN_NAME, b"trace_id": 42}]]),
        msgpack.packb([[b"", SPAN_NAME], [[[0, 1, 0, 42]]]]),
    ],
    ids=["v04", "v05"],
)
def test_trace_ids_for_span_returns_matching_trace(payload):
    assert _trace_ids_for_span(payload, SPAN_NAME) == {42}


@pytest.mark.parametrize("extra_args", [(), ("--lazy-apps", "--skip-atexit")], ids=["default", "lazy-apps"])
def test_ssi_traces_are_sent_from_prefork_workers(tmp_path, trace_agent, extra_args):
    agent_url, trace_available, received_trace_ids = trace_agent
    sitecustomize_dir = _prepare_injection_sources(tmp_path)
    telemetry_forwarder, telemetry_pids = _prepare_telemetry_forwarder(tmp_path)
    application_postfork_pids = tmp_path / "application-postfork-pids"

    env = os.environ.copy()
    env.pop("OTEL_TRACES_EXPORTER", None)
    env.pop("_DD_PY_SSI_INJECT", None)
    env.update(
        {
            "_DD_APM_TRACING_AGENTLESS_ENABLED": "false",
            "DD_INJECT_EXPERIMENTAL_OVERRIDE_USER_DDTRACE": "true",
            "DD_INJECT_FORCE": "true",
            "DD_INJECTION_ENABLED": "tracer",
            "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false",
            "DD_REMOTE_CONFIGURATION_ENABLED": "false",
            "DD_TELEMETRY_FORWARDER_PATH": str(telemetry_forwarder),
            "DD_TEST_SSI_PIDS": str(telemetry_pids),
            "DD_TEST_UWSGI_POSTFORK_PIDS": str(application_postfork_pids),
            "DD_TRACE_AGENT_URL": agent_url,
            "DD_TRACE_ENABLED": "true",
            "DD_TRACE_WRITER_INTERVAL_SECONDS": "0.1",
            "PYTHONPATH": os.pathsep.join((str(sitecustomize_dir), env.get("PYTHONPATH", ""))),
        }
    )

    http_socket = tmp_path / "uwsgi-http.sock"
    proc = subprocess.Popen(
        [
            "uwsgi",
            "--master",
            "--processes",
            "2",
            "--py-programname",
            sys.executable,
            "--enable-threads",
            "--die-on-term",
            "--need-app",
            "--http-socket",
            str(http_socket),
            "--wsgi-file",
            UWSGI_APP,
            *extra_args,
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    try:
        body = _request_when_ready(proc, str(http_socket))
        fields = dict(field.split(b"=", 1) for field in body.split(b";"))
        worker_pids = {int(pid) for pid in fields[b"workers"].split(b",")}
        expected_trace_id = int(fields[b"trace-id"]) & ((1 << 64) - 1)
        assert int(fields[b"pid"]) in worker_pids
        assert len(worker_pids) == 2
        if fields[b"ssi"] != b"1":
            output = _terminate(proc)
            raise AssertionError("SSI did not initialize in the uWSGI worker: %r" % output)
        with trace_available:
            if not trace_available.wait_for(lambda: expected_trace_id in received_trace_ids, timeout=TIMEOUT):
                output = _terminate(proc)
                raise AssertionError("uWSGI worker traces did not reach the trace agent: %r" % output)

        deadline = time.monotonic() + TIMEOUT
        injection_results = {}
        while not worker_pids <= injection_results.keys() and time.monotonic() < deadline:
            if telemetry_pids.exists():
                injection_results = {
                    int(pid): result
                    for pid, result in (line.split(":", 1) for line in telemetry_pids.read_text().splitlines())
                }
            time.sleep(0.1)
        if not worker_pids <= injection_results.keys():
            output = _terminate(proc)
            raise AssertionError(
                "SSI telemetry did not report both workers: %r; output: %r" % (injection_results, output)
            )
        assert all(injection_results[pid] in ("success", "success_forced") for pid in worker_pids)
        assert proc.pid not in injection_results

        if not extra_args:
            postfork_pids = {int(pid) for pid in application_postfork_pids.read_text().splitlines()}
            assert postfork_pids == worker_pids
    finally:
        _terminate(proc)
