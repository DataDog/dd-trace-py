from http.client import HTTPConnection
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import os
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
    trace_received = threading.Event()
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
                trace_ids.update(_trace_ids_for_span(body, SPAN_NAME))
                if trace_ids:
                    trace_received.set()
            self._respond(b'{"rate_by_service":{}}')

        do_POST = do_PUT

        def log_message(self, format_string, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    host, port = server.server_address
    yield "http://%s:%d" % (host, port), trace_received, trace_ids

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
    raise AssertionError("uWSGI did not serve a request: %r" % last_error)


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


def test_ssi_traces_are_sent_from_prefork_workers(tmp_path, trace_agent):
    agent_url, trace_received, received_trace_ids = trace_agent
    sitecustomize_dir = tmp_path / "ssi"
    sitecustomize_dir.mkdir()
    bootstrap_marker = tmp_path / "ssi-bootstrap"
    (sitecustomize_dir / "sitecustomize.py").write_text(
        "from pathlib import Path\n"
        "import uwsgi\n"
        'assert not hasattr(uwsgi, "opt")\n'
        "import ddtrace.auto\n"
        "Path(%r).write_text('loaded-before-uwsgi-options')\n" % str(bootstrap_marker)
    )

    env = os.environ.copy()
    env.pop("OTEL_TRACES_EXPORTER", None)
    env.update(
        {
            "_DD_PY_SSI_INJECT": "1",
            "_DD_APM_TRACING_AGENTLESS_ENABLED": "false",
            "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false",
            "DD_REMOTE_CONFIGURATION_ENABLED": "false",
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
            "--enable-threads",
            "--die-on-term",
            "--need-app",
            "--http-socket",
            str(http_socket),
            "--wsgi-file",
            UWSGI_APP,
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    try:
        body = _request_when_ready(proc, str(http_socket))
        assert body.startswith(b"trace-id=")
        expected_trace_id = int(body.removeprefix(b"trace-id=")) & ((1 << 64) - 1)
        assert bootstrap_marker.read_text() == "loaded-before-uwsgi-options"
        if not trace_received.wait(TIMEOUT):
            output = _terminate(proc)
            raise AssertionError("uWSGI worker created a span, but the trace agent received nothing: %r" % output)
        assert expected_trace_id in received_trace_ids
    finally:
        _terminate(proc)
