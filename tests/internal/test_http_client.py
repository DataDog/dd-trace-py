"""Tests for the native HTTP client (``ddtrace.internal.http_client.HTTPClient``).

The matrix mirrors NATIVE_HTTP_CLIENT_SPEC.md §6 — construction, happy-path
requests, HTTP-error semantics, transport variants (HTTP/HTTPS/UDS),
timeouts, retries, headers, GIL behavior, fork safety, error mapping,
concurrency / pooling, resource management, response shape, and URL handling.

The native FFI exposes a single ergonomic ``HTTPClient`` class: it owns the
base URL, default headers, timeout, and retry/error config, and its request
methods (``get``/``post``/``put``/``patch``/``delete``/``head``) take a path
that is joined onto the base URL. ``ddtrace.internal.http_client.HTTPClient``
is a thin subclass that injects the process-wide shared runtime, so callers
never pass a runtime explicitly.

Fixtures spin up `http.server.ThreadingHTTPServer` on port 0 so the suite is
``pytest -n auto`` friendly. Handler shapes mirror those in
``tests/tracer/test_writer.py`` (lines 555-720) but are inlined here because
that file imports ``msgpack``, which is not in the ``internal`` riot venv.
"""

from __future__ import annotations

import contextlib
import gzip
import http.server
import os
import socket
import socketserver
import sys
import tempfile
import threading
import time

import pytest

from ddtrace.internal.http_client import HTTPClient
from ddtrace.internal.native import ConnectionFailedError
from ddtrace.internal.native import HttpClientError
from ddtrace.internal.native import HttpIoError
from ddtrace.internal.native import HttpResponse
from ddtrace.internal.native import InvalidConfigError
from ddtrace.internal.native import RequestFailedError
from ddtrace.internal.native import TimedOutError


# --------------------------------------------------------------------------- #
# Test handlers and fixtures
# --------------------------------------------------------------------------- #
# DEV: these handler classes mirror the ones in tests.tracer.test_writer (lines
# 555-720) but are inlined here because importing tests.tracer.test_writer
# pulls in `msgpack` which is not in the `internal` riot venv. The shapes are
# small enough that duplication is cheaper than restructuring riotfile.py.


class _BaseHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    """Silence access logs so test output stays readable."""

    error_message_format = "%(message)s\n"
    error_content_type = "text/plain"

    def log_message(self, format, *args):  # noqa: A002
        pass


class _TimeoutAPIEndpointRequestHandlerTest(_BaseHTTPRequestHandler):
    """Sleeps longer than the test's configured timeout so callers see a
    `TimedOutError`.
    """

    def do_GET(self):
        time.sleep(5)

    def do_PUT(self):
        time.sleep(5)

    def do_POST(self):
        time.sleep(5)


class _ResetAPIEndpointRequestHandlerTest(_BaseHTTPRequestHandler):
    """Closes the connection without sending a response."""

    def do_GET(self):
        return

    def do_PUT(self):
        return

    def do_POST(self):
        return


class _IncompleteReadRequestHandlerTest(_BaseHTTPRequestHandler):
    """Sends a partial chunked response then closes — simulates the agent
    starting to respond but failing midway.
    """

    def _serve(self):
        self.send_response(200)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self.wfile.write(b"5\r\n")  # chunk size indicator
        self.wfile.write(b"Hello")  # partial chunk data
        # Missing: trailing \r\n + next chunk size + final 0\r\n\r\n
        self.wfile.flush()

    def do_GET(self):
        self._serve()

    def do_PUT(self):
        self._serve()

    def do_POST(self):
        self._serve()


class EchoHandler(_BaseHTTPRequestHandler):
    """Generic handler that echoes request method, headers, and body in a JSON-ish form."""

    def _respond(self, body: bytes = b"", status: int = 200, extra_headers=None) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/octet-stream")
        if extra_headers:
            for name, value in extra_headers:
                self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        self._respond(b"GET response")

    def do_HEAD(self):
        # HEAD: announce Content-Length but never write the body.
        self.send_response(200)
        self.send_header("Content-Length", "42")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b""
        self._respond(b"POST:" + body)

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b""
        self._respond(b"PUT:" + body)

    def do_DELETE(self):
        self._respond(b"DELETE response")

    def do_PATCH(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b""
        self._respond(b"PATCH:" + body)


class StatusCodeHandler(_BaseHTTPRequestHandler):
    """Returns a status code and body taken from URL path /<status>/<body>."""

    def _serve(self):
        parts = self.path.strip("/").split("/", 1)
        try:
            status = int(parts[0])
        except (IndexError, ValueError):
            status = 200
        body = (parts[1] if len(parts) > 1 else "").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._serve()

    def do_POST(self):
        # Discard body.
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        self._serve()


class _UDSHTTPServer(socketserver.UnixStreamServer, http.server.HTTPServer):  # type: ignore[misc]
    def server_bind(self):
        http.server.HTTPServer.server_bind(self)


class _FailNTimesHandler(_BaseHTTPRequestHandler):
    """Handler that fails (closes the connection) the first N attempts then
    returns 200. ``_attempts`` and ``_fail_n`` are set on the class before use.
    """

    _attempts = 0
    _fail_n = 0
    _final_body = b"OK"

    @classmethod
    def reset(cls, fail_n=0, final_body=b"OK"):
        cls._attempts = 0
        cls._fail_n = fail_n
        cls._final_body = final_body

    def _serve(self):
        # Pre-increment so attempts is 1-based for the first hit.
        type(self)._attempts += 1
        if type(self)._attempts <= type(self)._fail_n:
            # Close the connection without responding — looks like an IO error.
            return
        body = type(self)._final_body
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._serve()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        self._serve()


class _HeaderEchoHandler(_BaseHTTPRequestHandler):
    """Echoes received request headers as JSON in body."""

    def _respond(self):
        import json

        # Build list preserving order with duplicates.
        items = []
        for name in self.headers:
            for v in self.headers.get_all(name) or []:
                items.append((name, v))
        body = json.dumps(items).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._respond()


def _items_lower(json_body: bytes):
    """Helper: parse JSON list of [name, value] pairs with lowercased names.

    Python's http.server lowercases header names; this helper mirrors that so
    our assertions are case-stable.
    """
    import json

    items = json.loads(json_body)
    return [(name.lower(), value) for name, value in items]


class _AcceptCountingHandler(_BaseHTTPRequestHandler):
    """Tracks every TCP connection that hit the server.

    Uses HTTP/1.1 so connection reuse / keep-alive can happen — otherwise
    each handle_one_request() exits and the connection is torn down,
    defeating the point of the pooling assertion.
    """

    # DEV: setting protocol_version to HTTP/1.1 enables persistent connections
    # on the server side; reqwest's default is also HTTP/1.1 keep-alive.
    protocol_version = "HTTP/1.1"
    accept_count = 0
    accept_lock = threading.Lock()

    def setup(self):
        super().setup()
        with type(self).accept_lock:
            type(self).accept_count += 1

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"OK")


class _BodyCapturingHandler(_BaseHTTPRequestHandler):
    last_body = b""
    last_headers: dict = {}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        type(self).last_body = self.rfile.read(length) if length else b""
        type(self).last_headers = {k.lower(): v for k, v in self.headers.items()}
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"OK")


class _PathCapturingHandler(_BaseHTTPRequestHandler):
    last_path = ""

    def do_GET(self):
        type(self).last_path = self.path
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"OK")


@pytest.fixture
def serve():
    """Factory fixture: start a ThreadingHTTPServer on a random port for the given
    handler class and return its base URL. All servers are shut down at teardown.
    Binds port 0 so the suite is ``pytest -n auto`` friendly.
    """
    started = []

    def _serve(handler_cls, host="127.0.0.1"):
        server = http.server.ThreadingHTTPServer((host, 0), handler_cls)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        started.append((server, thread))
        port = server.server_address[1]
        # IPv6 literal hosts must be wrapped in brackets in the URL.
        host_part = f"[{host}]" if ":" in host else host
        return f"http://{host_part}:{port}"

    yield _serve
    for server, thread in started:
        server.shutdown()
        thread.join(timeout=2)


@pytest.fixture
def make_client():
    """Factory fixture: build an :class:`HTTPClient` and track it for shutdown at
    teardown. All keyword arguments are forwarded to the constructor.
    """
    clients = []

    def _make(base_url, **kwargs):
        client = HTTPClient(base_url, **kwargs)
        clients.append(client)
        return client

    yield _make
    for client in clients:
        with contextlib.suppress(Exception):
            client.shutdown()


@pytest.fixture
def uds_serve():
    """Factory fixture: start a UDS HTTP server and return its socket path. The
    socket dir/file and server are cleaned up at teardown.
    """
    started = []
    dirs = []

    def _serve(handler_cls=EchoHandler, sock_filename="test.sock", dir_prefix="ddtrace-http-uds-"):
        sock_dir = tempfile.mkdtemp(prefix=dir_prefix)
        dirs.append(sock_dir)
        sock_path = os.path.join(sock_dir, sock_filename)
        server = _UDSHTTPServer(sock_path, handler_cls)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        started.append((server, thread, sock_path))
        return sock_path

    yield _serve
    for server, thread, sock_path in started:
        server.shutdown()
        thread.join(timeout=2)
        with contextlib.suppress(OSError):
            os.unlink(sock_path)
    for sock_dir in dirs:
        with contextlib.suppress(OSError):
            os.rmdir(sock_dir)


# --------------------------------------------------------------------------- #
# §6.1 Construction / config
# --------------------------------------------------------------------------- #


def test_unsupported_scheme_raises():
    with pytest.raises(InvalidConfigError):
        HTTPClient("ftp://localhost:8126")


def test_retry_zero_is_one_attempt_builds(serve, make_client):
    # Just verify it builds — actual retry-count behavior is exercised in §6.6.
    base = serve(EchoHandler)
    client = make_client(base, timeout_ms=500, retry=(0, 10, False))
    assert client.get("/info").status_code == 200


# --------------------------------------------------------------------------- #
# §6.2 Happy-path requests
# --------------------------------------------------------------------------- #


def test_get(serve, make_client):
    base = serve(EchoHandler)
    client = make_client(base)
    resp = client.get("/info")
    assert resp.status_code == 200
    assert resp.body() == b"GET response"


def test_post_with_body(serve, make_client):
    base = serve(EchoHandler)
    client = make_client(base)
    resp = client.post("/data", body=b"payload")
    assert resp.status_code == 200
    assert resp.body() == b"POST:payload"


def test_put(serve, make_client):
    base = serve(EchoHandler)
    client = make_client(base)
    resp = client.put("/data", body=b"v")
    assert resp.body() == b"PUT:v"


def test_delete(serve, make_client):
    base = serve(EchoHandler)
    client = make_client(base)
    resp = client.delete("/x")
    assert resp.body() == b"DELETE response"


def test_patch(serve, make_client):
    base = serve(EchoHandler)
    client = make_client(base)
    resp = client.patch("/x", body=b"diff")
    assert resp.body() == b"PATCH:diff"


def test_head_returns_no_body_even_with_content_length(serve, make_client):
    base = serve(EchoHandler)
    client = make_client(base)
    resp = client.head("/x")
    assert resp.status_code == 200
    assert resp.body() == b""


def test_response_headers_preserve_order_and_duplicates(serve, make_client):
    class DupHandler(_BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Set-Cookie", "a=1")
            self.send_header("Set-Cookie", "b=2")
            self.send_header("X-Order", "first")
            self.end_headers()
            self.wfile.write(b"")

    base = serve(DupHandler)
    client = make_client(base)
    resp = client.get("/")
    cookies = [v for k, v in resp.headers if k.lower() == "set-cookie"]
    assert cookies == ["a=1", "b=2"]


def test_header_case_insensitive(serve, make_client):
    base = serve(EchoHandler)
    client = make_client(base)
    resp = client.get("/")
    assert resp.header("CONTENT-LENGTH") == resp.header("content-length")
    assert resp.header("Content-Length") == resp.header("content-length")


def test_empty_body_request(serve, make_client):
    base = serve(EchoHandler)
    client = make_client(base)
    resp = client.post("/data")  # no body
    assert resp.body() == b"POST:"


def test_empty_body_response(serve, make_client):
    class EmptyHandler(_BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(204)
            self.end_headers()

    base = serve(EmptyHandler)
    client = make_client(base)
    resp = client.post("/")
    assert resp.status_code == 204
    assert resp.body() == b""


def test_large_body_request_roundtrip(serve, make_client):
    base = serve(EchoHandler)
    client = make_client(base, timeout_ms=20_000)
    payload = b"X" * (10 * 1024 * 1024)  # 10 MB
    resp = client.post("/", body=payload)
    assert resp.status_code == 200
    assert resp.body() == b"POST:" + payload


def test_large_body_response_roundtrip(serve, make_client):
    class LargeHandler(_BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"Y" * (10 * 1024 * 1024)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    base = serve(LargeHandler)
    client = make_client(base, timeout_ms=20_000)
    resp = client.get("/")
    assert len(resp.body()) == 10 * 1024 * 1024


def test_binary_body_preserved(serve, make_client):
    # Use bytes containing every possible byte value.
    payload = bytes(range(256))
    base = serve(EchoHandler)
    client = make_client(base)
    resp = client.post("/", body=payload)
    assert resp.body() == b"POST:" + payload


def test_body_memoized(serve, make_client):
    # Repeated .body() calls should return the same PyBytes object
    # (memoization via PyOnceLock).
    base = serve(EchoHandler)
    client = make_client(base)
    resp = client.get("/")
    b1 = resp.body()
    b2 = resp.body()
    assert b1 is b2


def test_bytearray_body_accepted(serve, make_client):
    base = serve(EchoHandler)
    client = make_client(base)
    # DEV: stub narrows to `bytes`; PyBackedBytes accepts bytearray at runtime.
    resp = client.post("/", body=bytearray(b"abc"))  # type: ignore[arg-type]
    assert resp.body() == b"POST:abc"


def test_memoryview_body_rejected(serve, make_client):
    # DEV: PyO3 0.28's PyBackedBytes accepts bytes / bytearray but NOT
    # memoryview — callers needing memoryview pass `bytes(view)` themselves.
    # Pin this behavior so any future change requires an explicit decision.
    base = serve(EchoHandler)
    client = make_client(base)
    with pytest.raises(TypeError):
        client.post("/", body=memoryview(b"abc"))  # type: ignore[arg-type]


def test_datadog_container_tags_hash_header(serve, make_client):
    class HashHandler(_BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Datadog-Container-Tags-Hash", "abc123")
            self.end_headers()

    base = serve(HashHandler)
    client = make_client(base)
    resp = client.get("/info")
    assert resp.header("datadog-container-tags-hash") == "abc123"


# --------------------------------------------------------------------------- #
# §6.3 HTTP error semantics
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", [400, 403, 404, 500, 503])
def test_status_raises_request_failed_error(serve, make_client, status):
    base = serve(StatusCodeHandler)
    client = make_client(base)
    with pytest.raises(RequestFailedError) as exc_info:
        client.get(f"/{status}/bodytext")
    assert exc_info.value.status == status
    assert exc_info.value.body == "bodytext"


def test_404_with_empty_body(serve, make_client):
    base = serve(StatusCodeHandler)
    client = make_client(base)
    with pytest.raises(RequestFailedError) as exc_info:
        client.get("/404/")
    assert exc_info.value.status == 404
    assert exc_info.value.body == ""


def test_4xx_with_large_json_body_preserved(serve, make_client):
    body_text = '{"error":"' + ("x" * 4000) + '"}'

    class JSONErrHandler(_BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(400)
            payload = body_text.encode("utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    base = serve(JSONErrHandler)
    client = make_client(base)
    with pytest.raises(RequestFailedError) as exc_info:
        client.get("/")
    assert exc_info.value.status == 400
    assert exc_info.value.body == body_text


def test_treat_http_errors_off_returns_response(serve, make_client):
    base = serve(StatusCodeHandler)
    client = make_client(base, treat_http_errors_as_errors=False)
    resp = client.get("/503/oops")
    assert isinstance(resp, HttpResponse)
    assert resp.status_code == 503
    assert resp.body() == b"oops"


def test_truncated_body_raises_io_error(serve, make_client):
    # _IncompleteReadRequestHandlerTest sends a partial chunked response.
    base = serve(_IncompleteReadRequestHandlerTest)
    client = make_client(base, timeout_ms=2000)
    with pytest.raises((HttpIoError, ConnectionFailedError)):
        client.post("/", body=b"x")


# --------------------------------------------------------------------------- #
# §6.4 Transport variants
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    os.environ.get("CI_OFFLINE") == "1",
    reason="Network smoke test (set CI_OFFLINE=1 to skip)",
)
def test_https_smoke(make_client):
    """Smoke test against example.com — gated by CI_OFFLINE env var."""
    try:
        client = make_client("https://example.com", timeout_ms=10_000)
        resp = client.get("/")
        assert resp.status_code in (200, 301, 302)
    except (ConnectionFailedError, HttpIoError, TimedOutError):
        pytest.skip("Network unavailable")


def test_ipv6_loopback_roundtrip(make_client):
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.close()
    except OSError:
        pytest.skip("IPv6 not supported on this host")

    class _IPv6Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        address_family = socket.AF_INET6

    srv = _IPv6Server(("::1", 0), EchoHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        client = make_client(f"http://[::1]:{srv.server_address[1]}")
        resp = client.get("/")
        assert resp.status_code == 200
    finally:
        srv.shutdown()
        thread.join(timeout=2)


# --------------------------------------------------------------------------- #
# §6.4 UDS transport
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(sys.platform == "win32", reason="UDS is unix-only")
def test_uds_happy_path(uds_serve, make_client):
    sock_path = uds_serve()
    client = make_client("unix://" + sock_path, timeout_ms=5000)
    resp = client.get("/info")
    assert resp.status_code == 200
    assert resp.body() == b"GET response"


@pytest.mark.skipif(sys.platform == "win32", reason="UDS is unix-only")
def test_uds_missing_path(make_client):
    client = make_client("unix:///nonexistent/uds.sock", timeout_ms=1000)
    with pytest.raises((ConnectionFailedError, HttpIoError, TimedOutError)):
        client.get("/info")


@pytest.mark.skipif(sys.platform == "win32", reason="UDS is unix-only")
def test_uds_stale_socket(make_client):
    # Create a file at the path but with no listener — connection should fail.
    sock_dir = tempfile.mkdtemp(prefix="ddtrace-http-uds-stale-")
    sock_path = os.path.join(sock_dir, "stale.sock")
    # Open and close a socket so the file exists but nobody is listening.
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.bind(sock_path)
        # No listen() / accept() — connection attempts will fail.
        client = make_client("unix://" + sock_path, timeout_ms=1000)
        with pytest.raises((ConnectionFailedError, HttpIoError, TimedOutError)):
            client.get("/info")
    finally:
        s.close()
        with contextlib.suppress(OSError):
            os.unlink(sock_path)
        with contextlib.suppress(OSError):
            os.rmdir(sock_dir)


@pytest.mark.skipif(sys.platform == "win32", reason="UDS is unix-only")
def test_uds_regular_file(make_client):
    # A regular file at the socket path should not panic.
    with tempfile.NamedTemporaryFile() as f:
        client = make_client("unix://" + f.name, timeout_ms=1000)
        with pytest.raises((ConnectionFailedError, HttpIoError, TimedOutError)):
            client.get("/info")


@pytest.mark.skipif(sys.platform == "win32", reason="UDS is unix-only")
def test_uds_directory(make_client):
    # A directory at the socket path should not panic.
    d = tempfile.mkdtemp(prefix="ddtrace-http-uds-dir-")
    try:
        client = make_client("unix://" + d, timeout_ms=1000)
        with pytest.raises((ConnectionFailedError, HttpIoError, TimedOutError)):
            client.get("/info")
    finally:
        os.rmdir(d)


@pytest.mark.skipif(sys.platform == "win32", reason="UDS is unix-only")
def test_uds_long_path(make_client):
    # Linux sun_path limit is 108 bytes; create a path longer than that.
    # Should fail gracefully, not panic.
    long_segment = "a" * 50
    path = os.path.join(tempfile.gettempdir(), long_segment, long_segment, long_segment, "sock")
    # No need to create the path — we expect a clean error either way.
    client = make_client("unix://" + path, timeout_ms=500)
    with pytest.raises((ConnectionFailedError, HttpIoError, TimedOutError)):
        client.get("/info")


@pytest.mark.skipif(sys.platform == "win32", reason="UDS is unix-only")
def test_uds_path_with_spaces(uds_serve, make_client):
    sock_path = uds_serve(dir_prefix="ddtrace http uds spaces-", sock_filename="with spaces.sock")
    client = make_client("unix://" + sock_path, timeout_ms=5000)
    resp = client.get("/info")
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# §6.5 Timeouts
# --------------------------------------------------------------------------- #


def test_client_timeout_fires(serve, make_client):
    base = serve(_TimeoutAPIEndpointRequestHandlerTest)
    client = make_client(base, timeout_ms=300)
    with pytest.raises(TimedOutError):
        client.post("/")


def test_per_request_timeout_shorter_than_client(serve, make_client):
    base = serve(_TimeoutAPIEndpointRequestHandlerTest)
    client = make_client(base, timeout_ms=10_000)
    with pytest.raises(TimedOutError):
        client.post("/", timeout_ms=300)


def test_per_request_timeout_longer_than_client(serve, make_client):
    # A per-request timeout longer than the client timeout should win.
    # Use a server that responds quickly to verify success, not failure.
    base = serve(EchoHandler)
    client = make_client(base, timeout_ms=100)
    resp = client.get("/", timeout_ms=5000)
    assert resp.status_code == 200


def test_timeout_within_tolerance(serve, make_client):
    base = serve(_TimeoutAPIEndpointRequestHandlerTest)
    client = make_client(base, timeout_ms=500)
    start = time.monotonic()
    with pytest.raises(TimedOutError):
        client.post("/")
    elapsed = (time.monotonic() - start) * 1000
    assert 300 <= elapsed <= 1500, f"timeout fired at {elapsed}ms, expected ~500ms"


# --------------------------------------------------------------------------- #
# §6.6 Retries
# --------------------------------------------------------------------------- #


def test_retry_succeeds_after_failures(serve, make_client):
    class _H(_FailNTimesHandler):
        pass

    _H.reset(fail_n=2, final_body=b"recovered")
    base = serve(_H)
    client = make_client(base, timeout_ms=2000, retry=(3, 10, False))
    resp = client.post("/")
    assert resp.status_code == 200
    assert _H._attempts == 3


def test_retry_all_fail_terminal_error(serve, make_client):
    # A server that always fails: counts attempts and raises after.
    class _H(_FailNTimesHandler):
        pass

    # fail_n large enough that all attempts fail; we'll expect an error.
    _H.reset(fail_n=99)
    base = serve(_H)
    client = make_client(base, timeout_ms=1000, retry=(2, 10, False))
    with pytest.raises((HttpIoError, ConnectionFailedError)):
        client.post("/")
    # max_retries=2 means 1 initial + 2 retries = 3 attempts
    assert _H._attempts == 3


def test_retry_zero_is_one_attempt(serve, make_client):
    class _H(_FailNTimesHandler):
        pass

    _H.reset(fail_n=99)
    base = serve(_H)
    client = make_client(base, timeout_ms=1000, retry=(0, 10, False))
    with pytest.raises((HttpIoError, ConnectionFailedError)):
        client.post("/")
    assert _H._attempts == 1


def test_404_with_retry_retries(serve, make_client):
    # libdd default: 4xx ARE retried (gotcha).
    class _StatusH(StatusCodeHandler):
        _hits = 0

        def do_GET(self):
            _StatusH._hits += 1
            self._serve()

    _StatusH._hits = 0
    base = serve(_StatusH)
    client = make_client(base, timeout_ms=2000, retry=(3, 5, False))
    with pytest.raises(RequestFailedError):
        client.get("/404/notfound")
    assert _StatusH._hits == 4  # 1 + 3 retries


def test_404_with_treat_off_no_retries(serve, make_client):
    class _StatusH(StatusCodeHandler):
        _hits = 0

        def do_GET(self):
            _StatusH._hits += 1
            self._serve()

    _StatusH._hits = 0
    base = serve(_StatusH)
    client = make_client(base, timeout_ms=2000, treat_http_errors_as_errors=False, retry=(3, 5, False))
    resp = client.get("/404/notfound")
    assert resp.status_code == 404
    assert _StatusH._hits == 1  # no retry because not an error variant


def test_retry_delay_respected(serve, make_client):
    # With jitter disabled, the delay must be >= initial_delay_ms.
    class _H(_FailNTimesHandler):
        pass

    _H.reset(fail_n=2)
    base = serve(_H)
    client = make_client(base, timeout_ms=5000, retry=(2, 200, False))
    start = time.monotonic()
    client.post("/")
    elapsed = (time.monotonic() - start) * 1000
    # 200ms before retry 1 + 400ms before retry 2 = at least 600ms.
    assert elapsed >= 400, f"retries elapsed only {elapsed}ms, expected >= 400"


def test_retry_on_connection_refused(make_client):
    # Bind a socket, close it — connecting will fail.
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    client = make_client(f"http://127.0.0.1:{port}", timeout_ms=1000, retry=(2, 5, False))
    with pytest.raises(ConnectionFailedError):
        client.get("/")


def test_connection_reset_mid_response(serve, make_client):
    base = serve(_ResetAPIEndpointRequestHandlerTest)
    client = make_client(base, timeout_ms=1000, retry=(0, 10, False))
    with pytest.raises((HttpIoError, ConnectionFailedError)):
        client.post("/")


# --------------------------------------------------------------------------- #
# §6.7 Headers
# --------------------------------------------------------------------------- #


def test_single_header(serve, make_client):
    base = serve(_HeaderEchoHandler)
    client = make_client(base)
    resp = client.get("/", headers=[("X-One", "v1")])
    assert ("x-one", "v1") in _items_lower(resp.body())


def test_multiple_headers(serve, make_client):
    base = serve(_HeaderEchoHandler)
    client = make_client(base)
    resp = client.get("/", headers=[("A", "1"), ("B", "2")])
    items = _items_lower(resp.body())
    assert ("a", "1") in items and ("b", "2") in items


def test_unicode_header_value(serve, make_client):
    # UTF-8 header values must encode correctly (reqwest validates ASCII
    # but the underlying http crate allows opaque bytes). Either rejected at
    # send time or sent through; both are acceptable here.
    base = serve(_HeaderEchoHandler)
    client = make_client(base)
    try:
        resp = client.get("/", headers=[("X", "hello-世界")])
        assert resp.status_code == 200
    except (ConnectionFailedError, InvalidConfigError, HttpIoError):
        pass


@pytest.mark.parametrize("bad_value", ["v\r\nX-Injected: 1", "v\nX-Injected: 1", "v\x00v"])
def test_crlf_injection_rejected(serve, make_client, bad_value):
    # CRLF / LF / NUL must be rejected to prevent header injection.
    base = serve(EchoHandler)
    client = make_client(base)
    with pytest.raises((InvalidConfigError, ConnectionFailedError, HttpIoError, ValueError)):
        client.get("/", headers=[("X", bad_value)])


def test_empty_header_value_accepted(serve, make_client):
    base = serve(_HeaderEchoHandler)
    client = make_client(base)
    resp = client.get("/", headers=[("X-Empty", "")])
    items = _items_lower(resp.body())
    assert ("x-empty", "") in items


def test_many_distinct_headers(serve, make_client):
    # DEV: Python's http.server caps at ~100 total request headers (HTTP 431
    # otherwise); 50 user headers is well within that budget while still
    # being a meaningful "lots of headers" smoke test.
    headers = [(f"X-H{i}", f"v{i}") for i in range(50)]
    base = serve(_HeaderEchoHandler)
    client = make_client(base)
    resp = client.get("/", headers=headers)
    items = _items_lower(resp.body())
    for name, value in headers:
        assert (name.lower(), value) in items


# --------------------------------------------------------------------------- #
# §6.9 GIL behavior / threading
# --------------------------------------------------------------------------- #


def test_gil_released_during_send(serve, make_client):
    # While one thread is in a request, another thread should keep
    # incrementing a counter — proving the GIL is released.
    base = serve(_TimeoutAPIEndpointRequestHandlerTest)
    client = make_client(base, timeout_ms=600)
    counter = [0]
    stop = threading.Event()

    def incr():
        while not stop.is_set():
            counter[0] += 1

    t = threading.Thread(target=incr, daemon=True)
    t.start()
    try:
        with pytest.raises(TimedOutError):
            client.post("/")
    finally:
        stop.set()
        t.join(timeout=1)
    # The counter thread should have made significant progress.
    assert counter[0] > 1000, f"GIL appears to be held: only {counter[0]} iterations"


def test_concurrent_threads_share_client(serve, make_client):
    base = serve(EchoHandler)
    client = make_client(base)
    errors = []
    successes = [0]
    lock = threading.Lock()

    def worker():
        try:
            for _ in range(10):
                resp = client.get("/")
                if resp.status_code == 200:
                    with lock:
                        successes[0] += 1
        except Exception as e:
            with lock:
                errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"threading errors: {errors}"
    assert successes[0] == 100


# --------------------------------------------------------------------------- #
# §6.10 Fork safety
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(sys.platform == "win32", reason="fork() not available on Windows")
@pytest.mark.subprocess(env={"PYTHONWARNINGS": "ignore::DeprecationWarning:os"}, err=None)
def test_fork_parent_and_child_can_send():
    import http.server
    import os
    import socket
    import threading

    from ddtrace.internal.http_client import HTTPClient

    class EchoHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a, **k):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    # Bind ports up front so each side knows where to connect.
    s1 = socket.socket()
    s1.bind(("127.0.0.1", 0))
    p1 = s1.getsockname()[1]
    s1.close()
    s2 = socket.socket()
    s2.bind(("127.0.0.1", 0))
    p2 = s2.getsockname()[1]
    s2.close()

    # Build a client in the parent BEFORE forking — fork hooks should keep
    # things sane in both processes.
    _ = HTTPClient(f"http://127.0.0.1:{p1}", timeout_ms=2000)

    pid = os.fork()
    if pid == 0:
        # Child: spin up our own server, build a fresh client, send a request.
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", p2), EchoHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            client = HTTPClient(f"http://127.0.0.1:{p2}", timeout_ms=2000)
            resp = client.get("/")
            assert resp.status_code == 200
            assert resp.body() == b"ok"
        finally:
            srv.shutdown()
        os._exit(0)
    else:
        # Parent: spin up our own server on p1.
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", p1), EchoHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            client = HTTPClient(f"http://127.0.0.1:{p1}", timeout_ms=2000)
            resp = client.get("/")
            assert resp.status_code == 200
            assert resp.body() == b"ok"
        finally:
            srv.shutdown()
        _, status = os.waitpid(pid, 0)
        assert os.WEXITSTATUS(status) == 0


# --------------------------------------------------------------------------- #
# §6.11 Error mapping
# --------------------------------------------------------------------------- #


def test_all_subclass_http_client_error():
    assert issubclass(ConnectionFailedError, HttpClientError)
    assert issubclass(TimedOutError, HttpClientError)
    assert issubclass(RequestFailedError, HttpClientError)
    assert issubclass(InvalidConfigError, HttpClientError)
    assert issubclass(HttpIoError, HttpClientError)


def test_connection_refused(make_client):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    client = make_client(f"http://127.0.0.1:{port}", timeout_ms=1000)
    with pytest.raises(ConnectionFailedError) as exc_info:
        client.get("/")
    # Just verify the exception carries a message; the exact wording is
    # libdd / reqwest implementation detail.
    assert str(exc_info.value)


def test_503_response(serve, make_client):
    base = serve(StatusCodeHandler)
    client = make_client(base)
    with pytest.raises(RequestFailedError) as exc_info:
        client.get("/503/oops")
    assert exc_info.value.status == 503
    assert exc_info.value.body == "oops"


# --------------------------------------------------------------------------- #
# §6.12 Concurrency / pooling
# --------------------------------------------------------------------------- #


def test_pooling_default_few_connections(serve, make_client):
    _AcceptCountingHandler.accept_count = 0
    base = serve(_AcceptCountingHandler)
    client = make_client(base)
    for _ in range(50):
        resp = client.get("/")
        assert resp.status_code == 200
    # With pooling on, we expect far fewer than 50 connections.
    assert _AcceptCountingHandler.accept_count <= 10, (
        f"expected pooling, got {_AcceptCountingHandler.accept_count} connections"
    )


# --------------------------------------------------------------------------- #
# §6.14 Resource management
# --------------------------------------------------------------------------- #


def test_no_fd_leak_when_building_many_clients(serve):
    psutil = pytest.importorskip("psutil")
    base = serve(EchoHandler)
    proc = psutil.Process()
    baseline = proc.num_fds() if hasattr(proc, "num_fds") else len(proc.open_files())
    for _ in range(100):
        c = HTTPClient(base, timeout_ms=500)
        c.shutdown()
        del c
    # Allow a small slack for runtime-internal bookkeeping.
    after = proc.num_fds() if hasattr(proc, "num_fds") else len(proc.open_files())
    assert after - baseline < 20, f"possible FD leak: {baseline} -> {after}"


# --------------------------------------------------------------------------- #
# §6.16 Request body encoding (Datadog payload shapes)
# --------------------------------------------------------------------------- #


def test_pre_gzipped_body_sent_verbatim(serve, make_client):
    raw = b'{"k":"v"}' * 100
    gzipped = gzip.compress(raw)
    base = serve(_BodyCapturingHandler)
    client = make_client(base)
    resp = client.post(
        "/",
        headers=[("Content-Type", "application/json"), ("Content-Encoding", "gzip")],
        body=gzipped,
    )
    assert resp.status_code == 200
    assert _BodyCapturingHandler.last_body == gzipped
    assert _BodyCapturingHandler.last_headers.get("content-encoding") == "gzip"


def test_msgpack_binary_body_byte_exact(serve, make_client):
    # Synthetic msgpack-ish bytes.
    payload = b"\x81\xa3foo\x01" + bytes(range(64))
    base = serve(_BodyCapturingHandler)
    client = make_client(base)
    client.post(
        "/",
        headers=[("Content-Type", "application/msgpack")],
        body=payload,
    )
    assert _BodyCapturingHandler.last_body == payload


# --------------------------------------------------------------------------- #
# §6.17 Response decompression (pinned: no auto-decompress)
# --------------------------------------------------------------------------- #


def test_gzip_response_returned_raw(serve, make_client):
    raw = b"hello world! " * 100
    gzipped = gzip.compress(raw)

    class _GZHandler(_BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(gzipped)))
            self.end_headers()
            self.wfile.write(gzipped)

    base = serve(_GZHandler)
    client = make_client(base)
    resp = client.get("/")
    assert resp.body() == gzipped
    assert resp.header("content-encoding") == "gzip"


# --------------------------------------------------------------------------- #
# §6.18 URL handling
# --------------------------------------------------------------------------- #


def test_query_string_preserved(serve, make_client):
    base = serve(_PathCapturingHandler)
    client = make_client(base)
    client.get("/v0.4/traces?a=1&b=foo%20bar")
    assert "?a=1&b=foo%20bar" in _PathCapturingHandler.last_path


def test_fragment_not_sent(serve, make_client):
    base = serve(_PathCapturingHandler)
    client = make_client(base)
    client.get("/info#section")
    # HTTP standard: fragments are client-side only.
    assert "#" not in _PathCapturingHandler.last_path


def test_trailing_slash_distinct(serve, make_client):
    base = serve(_PathCapturingHandler)
    client = make_client(base)
    client.get("/v0.4/traces")
    no_slash = _PathCapturingHandler.last_path
    client.get("/v0.4/traces/")
    with_slash = _PathCapturingHandler.last_path
    assert no_slash != with_slash


def test_percent_encoded_path_preserved(serve, make_client):
    base = serve(_PathCapturingHandler)
    client = make_client(base)
    client.get("/path%2Fwith%20encoding")
    assert "%2F" in _PathCapturingHandler.last_path or "%2f" in _PathCapturingHandler.last_path.lower()


# --------------------------------------------------------------------------- #
# §6.21 Lifecycle
# --------------------------------------------------------------------------- #


def test_shutdown_blocks_send(serve, make_client):
    base = serve(EchoHandler)
    client = make_client(base, timeout_ms=500)
    client.shutdown()
    with pytest.raises(ValueError):
        client.get("/info")


def test_http_client_error_is_subclassable():
    class MyError(HttpClientError):
        pass

    # Just verify subclass relationship works.
    assert issubclass(MyError, HttpClientError)


# --------------------------------------------------------------------------- #
# §6.22 Datadog response shape sanity
# --------------------------------------------------------------------------- #


def test_ok_body(serve, make_client):
    class _OKHandler(_BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"OK")

    base = serve(_OKHandler)
    client = make_client(base)
    resp = client.post("/v0.4/traces")
    assert resp.body() == b"OK"


def test_202_empty_body_not_raised(serve, make_client):
    class _202Handler(_BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(202)
            self.end_headers()

    base = serve(_202Handler)
    client = make_client(base)
    resp = client.post("/telemetry")
    assert resp.status_code == 202
    assert resp.body() == b""


def test_204_empty_body(serve, make_client):
    class _204Handler(_BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(204)
            self.end_headers()

    base = serve(_204Handler)
    client = make_client(base)
    resp = client.post("/v0.7/config")
    assert resp.status_code == 204
    assert resp.body() == b""


def test_json_envelope_byte_exact(serve, make_client):
    # Realistic /info payload sample.
    payload = b'{"endpoints":["/v0.4/traces","/v0.7/config"],"client_drop_p0s":true,"version":"7.50.0"}'

    class _InfoHandler(_BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    base = serve(_InfoHandler)
    client = make_client(base)
    resp = client.get("/info")
    assert resp.body() == payload


# --------------------------------------------------------------------------- #
# HTTPClient base-URL + default-header behavior
# --------------------------------------------------------------------------- #


def test_relative_path_get(serve, make_client):
    base = serve(EchoHandler)
    client = make_client(base)
    resp = client.get("/info")
    assert resp.status_code == 200
    assert resp.body() == b"GET response"


def test_path_without_leading_slash(serve, make_client):
    base = serve(EchoHandler)
    client = make_client(base)
    assert client.get("info").status_code == 200


def test_trailing_slash_base_url(serve, make_client):
    base = serve(EchoHandler)
    client = make_client(base + "/")  # base with a trailing slash must not double up
    resp = client.get("/info")
    assert resp.status_code == 200


def test_default_headers_sent(serve, make_client):
    base = serve(_HeaderEchoHandler)
    client = make_client(base, headers=[("Datadog-Meta-Lang", "python")])
    resp = client.get("/")
    items = _items_lower(resp.body())
    assert ("datadog-meta-lang", "python") in items


def test_per_request_header_overrides_default(serve, make_client):
    base = serve(_HeaderEchoHandler)
    client = make_client(base, headers=[("X-Env", "default")])
    resp = client.get("/", headers=[("X-Env", "override")])
    values = [v for k, v in _items_lower(resp.body()) if k == "x-env"]
    assert values == ["override"]


def test_per_request_header_merges_with_defaults(serve, make_client):
    base = serve(_HeaderEchoHandler)
    client = make_client(base, headers=[("X-Default", "d")])
    resp = client.get("/", headers=[("X-Extra", "e")])
    items = _items_lower(resp.body())
    assert ("x-default", "d") in items
    assert ("x-extra", "e") in items


def test_default_errors_raise(serve, make_client):
    base = serve(StatusCodeHandler)
    client = make_client(base)
    with pytest.raises(RequestFailedError) as exc_info:
        client.get("/404/missing")
    assert exc_info.value.status == 404


@pytest.mark.skipif(sys.platform == "win32", reason="UDS is unix-only")
def test_unix_socket_base_url(uds_serve, make_client):
    sock_path = uds_serve()
    client = make_client("unix://" + sock_path)
    resp = client.get("/info")
    assert resp.status_code == 200
    assert resp.body() == b"GET response"


def test_base_url_path_prefix_preserved(serve, make_client):
    # A base URL with a path prefix prepends it (NOT urljoin's absolute-path replace).
    seen = []

    class _PathHandler(_BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    base = serve(_PathHandler)
    client = make_client(base + "/prefix")
    client.get("/v0.4/traces")
    assert seen == ["/prefix/v0.4/traces"]
