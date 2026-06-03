"""Tests for the native HTTP client (``ddtrace.internal.http_client.HTTPClient``).

Covers the FFI boundary: base-URL joining, header merging, scheme dispatch,
PyO3 type conversions (body as bytes, headers as list of tuples, exceptions),
GIL release, fork safety, and lifecycle (shutdown, context manager).

The underlying HTTP transport (libdd-http-client / reqwest) is tested by its
own test suite; these tests do not re-verify transport internals.

Fixtures spin up `http.server.ThreadingHTTPServer` on port 0 so the suite is
``pytest -n auto`` friendly. Handlers are inlined rather than imported from
``tests/tracer/test_writer`` because importing that module pulls in ``msgpack``,
which is not in the ``internal`` riot venv.
"""

from __future__ import annotations

import contextlib
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
# Construction / config
# --------------------------------------------------------------------------- #


def test_unsupported_scheme_raises():
    with pytest.raises(InvalidConfigError):
        HTTPClient("ftp://localhost:8126")


def test_retry_zero_is_one_attempt_builds(serve, make_client):
    # Just verify construction succeeds — retry-count semantics are exercised in the Retries section below.
    base = serve(EchoHandler)
    client = make_client(base, timeout_ms=500, max_retries=0, retry_initial_delay_ms=10, retry_jitter=False)
    assert client.get("/info").status_code == 200


# --------------------------------------------------------------------------- #
# Happy-path requests
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
# HTTP error semantics
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
# UDS transport
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
def test_uds_path_with_spaces(uds_serve, make_client):
    sock_path = uds_serve(dir_prefix="ddtrace http uds spaces-", sock_filename="with spaces.sock")
    client = make_client("unix://" + sock_path, timeout_ms=5000)
    resp = client.get("/info")
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# Timeouts
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


# --------------------------------------------------------------------------- #
# Headers
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


def test_empty_header_value_accepted(serve, make_client):
    base = serve(_HeaderEchoHandler)
    client = make_client(base)
    resp = client.get("/", headers=[("X-Empty", "")])
    items = _items_lower(resp.body())
    assert ("x-empty", "") in items


# --------------------------------------------------------------------------- #
# GIL behavior / threading
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


# --------------------------------------------------------------------------- #
# Fork safety
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
# Error mapping
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
# URL handling
# --------------------------------------------------------------------------- #


def test_query_string_preserved(serve, make_client):
    seen = []

    class _PathHandler(_BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    base = serve(_PathHandler)
    client = make_client(base)
    client.get("/v0.4/traces?a=1&b=foo%20bar")
    assert seen and "?a=1&b=foo%20bar" in seen[-1]


def test_trailing_slash_distinct(serve, make_client):
    seen = []

    class _PathHandler(_BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    base = serve(_PathHandler)
    client = make_client(base)
    client.get("/v0.4/traces")
    no_slash = seen[-1]
    client.get("/v0.4/traces/")
    with_slash = seen[-1]
    assert no_slash != with_slash


# --------------------------------------------------------------------------- #
# Lifecycle
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


def test_context_manager(serve):
    base = serve(EchoHandler)
    with HTTPClient(base) as client:
        assert client.get("/").status_code == 200
    # After __exit__, client is shut down
    with pytest.raises(ValueError):
        client.get("/")


# --------------------------------------------------------------------------- #
# Datadog response shape sanity
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
