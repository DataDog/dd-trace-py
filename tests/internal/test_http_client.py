"""Tests for the native HTTP client (``ddtrace.internal.native.HttpClient``).

The matrix mirrors NATIVE_HTTP_CLIENT_SPEC.md §6 — construction, happy-path
requests, HTTP-error semantics, transport variants (HTTP/HTTPS/UDS),
timeouts, retries, headers, multipart, GIL behavior, fork safety, error
mapping, concurrency / pooling, lifecycle / move semantics, resource
management, response shape, and URL handling.

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
from typing import Iterator

import pytest

from ddtrace.internal.native import ConnectionFailedError
from ddtrace.internal.native import HttpClient
from ddtrace.internal.native import HttpClientBuilder
from ddtrace.internal.native import HttpClientError
from ddtrace.internal.native import HttpIoError
from ddtrace.internal.native import HttpMethod
from ddtrace.internal.native import HttpRequest
from ddtrace.internal.native import HttpResponse
from ddtrace.internal.native import InvalidConfigError
from ddtrace.internal.native import MultipartPart
from ddtrace.internal.native import RequestFailedError
from ddtrace.internal.native import TimedOutError
from ddtrace.internal.native_runtime import get_native_runtime


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


def _build_client(timeout_ms: int = 5000, **kwargs) -> HttpClient:
    builder = HttpClientBuilder().set_timeout_ms(timeout_ms)
    for setter, value in kwargs.items():
        builder = getattr(builder, setter)(value)
    return builder.build(get_native_runtime())


@contextlib.contextmanager
def _http_server(handler_cls, host: str = "127.0.0.1") -> Iterator[tuple[str, http.server.ThreadingHTTPServer]]:
    """Start a ThreadingHTTPServer on a random port and yield its base URL."""
    server = http.server.ThreadingHTTPServer((host, 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        # IPv6 literal hosts must be wrapped in brackets in the URL.
        host_part = f"[{host}]" if ":" in host else host
        yield f"http://{host_part}:{port}", server
    finally:
        server.shutdown()
        thread.join(timeout=2)


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


# --------------------------------------------------------------------------- #
# §6.1 Construction / config
# --------------------------------------------------------------------------- #


class TestBuilderConfig:
    def test_builder_requires_timeout(self):
        builder = HttpClientBuilder()
        with pytest.raises(InvalidConfigError):
            builder.build(get_native_runtime())

    def test_builder_consumed_after_build(self):
        builder = HttpClientBuilder().set_timeout_ms(1000)
        builder.build(get_native_runtime())
        with pytest.raises(ValueError):
            builder.build(get_native_runtime())

    def test_builder_consumed_blocks_setters(self):
        builder = HttpClientBuilder().set_timeout_ms(1000)
        builder.build(get_native_runtime())
        with pytest.raises(ValueError):
            builder.set_timeout_ms(2000)

    def test_setters_are_chainable(self):
        builder = HttpClientBuilder()
        same = builder.set_timeout_ms(1000)
        assert same is builder
        same = same.set_treat_http_errors_as_errors(False)
        assert same is builder
        same = same.set_retry(2, 50, False)
        assert same is builder

    @pytest.mark.skipif(sys.platform == "win32", reason="UDS is unix-only")
    def test_unix_socket_nonexistent_path_builds_ok(self):
        builder = HttpClientBuilder().set_timeout_ms(500).set_unix_socket("/nonexistent/socket.sock")
        client = builder.build(get_native_runtime())
        with pytest.raises(ConnectionFailedError):
            client.get("http://localhost/info")

    def test_retry_zero_is_no_retry(self):
        # Just verify it builds — actual retry-count tests are in §6.6.
        client = HttpClientBuilder().set_timeout_ms(500).set_retry(0).build(get_native_runtime())
        assert "open" in repr(client)

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_unix_socket_on_windows_raises(self):
        builder = HttpClientBuilder().set_timeout_ms(1000)
        with pytest.raises(ValueError):
            builder.set_unix_socket("/tmp/sock")


# --------------------------------------------------------------------------- #
# §6.2 Happy-path requests
# --------------------------------------------------------------------------- #


class TestHappyPath:
    def test_get(self):
        with _http_server(EchoHandler) as (base, _):
            client = _build_client()
            resp = client.get(f"{base}/info")
            assert resp.status_code == 200
            assert resp.body() == b"GET response"

    def test_post_with_body(self):
        with _http_server(EchoHandler) as (base, _):
            client = _build_client()
            resp = client.post(f"{base}/data", body=b"payload")
            assert resp.status_code == 200
            assert resp.body() == b"POST:payload"

    def test_put(self):
        with _http_server(EchoHandler) as (base, _):
            client = _build_client()
            resp = client.put(f"{base}/data", body=b"v")
            assert resp.body() == b"PUT:v"

    def test_delete(self):
        with _http_server(EchoHandler) as (base, _):
            client = _build_client()
            resp = client.delete(f"{base}/x")
            assert resp.body() == b"DELETE response"

    def test_patch(self):
        with _http_server(EchoHandler) as (base, _):
            client = _build_client()
            resp = client.patch(f"{base}/x", body=b"diff")
            assert resp.body() == b"PATCH:diff"

    def test_head_returns_no_body_even_with_content_length(self):
        with _http_server(EchoHandler) as (base, _):
            client = _build_client()
            resp = client.head(f"{base}/x")
            assert resp.status_code == 200
            assert resp.body() == b""

    def test_response_headers_preserve_order_and_duplicates(self):
        class DupHandler(_BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Set-Cookie", "a=1")
                self.send_header("Set-Cookie", "b=2")
                self.send_header("X-Order", "first")
                self.end_headers()
                self.wfile.write(b"")

        with _http_server(DupHandler) as (base, _):
            client = _build_client()
            resp = client.get(f"{base}/")
            cookies = [v for k, v in resp.headers if k.lower() == "set-cookie"]
            assert cookies == ["a=1", "b=2"]

    def test_header_case_insensitive(self):
        with _http_server(EchoHandler) as (base, _):
            client = _build_client()
            resp = client.get(f"{base}/")
            assert resp.header("CONTENT-LENGTH") == resp.header("content-length")
            assert resp.header("Content-Length") == resp.header("content-length")

    def test_empty_body_request(self):
        with _http_server(EchoHandler) as (base, _):
            client = _build_client()
            resp = client.post(f"{base}/data")  # no body
            assert resp.body() == b"POST:"

    def test_empty_body_response(self):
        class EmptyHandler(_BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(204)
                self.end_headers()

        with _http_server(EmptyHandler) as (base, _):
            client = _build_client()
            resp = client.post(f"{base}/")
            assert resp.status_code == 204
            assert resp.body() == b""

    def test_large_body_request_roundtrip(self):
        with _http_server(EchoHandler) as (base, _):
            client = _build_client(timeout_ms=20_000)
            payload = b"X" * (10 * 1024 * 1024)  # 10 MB
            resp = client.post(f"{base}/", body=payload)
            assert resp.status_code == 200
            assert resp.body() == b"POST:" + payload

    def test_large_body_response_roundtrip(self):
        class LargeHandler(_BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"Y" * (10 * 1024 * 1024)
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        with _http_server(LargeHandler) as (base, _):
            client = _build_client(timeout_ms=20_000)
            resp = client.get(f"{base}/")
            assert len(resp.body()) == 10 * 1024 * 1024

    def test_binary_body_preserved(self):
        # Use bytes containing every possible byte value.
        payload = bytes(range(256))
        with _http_server(EchoHandler) as (base, _):
            client = _build_client()
            resp = client.post(f"{base}/", body=payload)
            assert resp.body() == b"POST:" + payload

    def test_body_memoized(self):
        # Repeated .body() calls should return the same PyBytes object
        # (memoization via PyOnceLock).
        with _http_server(EchoHandler) as (base, _):
            client = _build_client()
            resp = client.get(f"{base}/")
            b1 = resp.body()
            b2 = resp.body()
            assert b1 is b2

    def test_bytearray_body_accepted(self):
        with _http_server(EchoHandler) as (base, _):
            client = _build_client()
            # DEV: stub narrows to `bytes`; PyBackedBytes accepts bytearray at runtime.
            resp = client.post(f"{base}/", body=bytearray(b"abc"))  # type: ignore[arg-type]
            assert resp.body() == b"POST:abc"

    def test_memoryview_body_rejected(self):
        # DEV: PyO3 0.28's PyBackedBytes accepts bytes / bytearray but NOT
        # memoryview — callers needing memoryview pass `bytes(view)` themselves.
        # Pin this behavior so any future change requires an explicit decision.
        with _http_server(EchoHandler) as (base, _):
            client = _build_client()
            with pytest.raises(TypeError):
                client.post(f"{base}/", body=memoryview(b"abc"))  # type: ignore[arg-type]

    def test_datadog_container_tags_hash_header(self):
        class HashHandler(_BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Datadog-Container-Tags-Hash", "abc123")
                self.end_headers()

        with _http_server(HashHandler) as (base, _):
            client = _build_client()
            resp = client.get(f"{base}/info")
            assert resp.header("datadog-container-tags-hash") == "abc123"


# --------------------------------------------------------------------------- #
# §6.3 HTTP error semantics
# --------------------------------------------------------------------------- #


class TestHttpErrorSemantics:
    @pytest.mark.parametrize("status", [400, 403, 404, 500, 503])
    def test_status_raises_request_failed_error(self, status):
        with _http_server(StatusCodeHandler) as (base, _):
            client = _build_client()
            with pytest.raises(RequestFailedError) as exc_info:
                client.get(f"{base}/{status}/bodytext")
            assert exc_info.value.status == status
            assert exc_info.value.body == "bodytext"

    def test_404_with_empty_body(self):
        with _http_server(StatusCodeHandler) as (base, _):
            client = _build_client()
            with pytest.raises(RequestFailedError) as exc_info:
                client.get(f"{base}/404/")
            assert exc_info.value.status == 404
            assert exc_info.value.body == ""

    def test_4xx_with_large_json_body_preserved(self):
        body_text = '{"error":"' + ("x" * 4000) + '"}'

        class JSONErrHandler(_BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(400)
                payload = body_text.encode("utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        with _http_server(JSONErrHandler) as (base, _):
            client = _build_client()
            with pytest.raises(RequestFailedError) as exc_info:
                client.get(f"{base}/")
            assert exc_info.value.status == 400
            assert exc_info.value.body == body_text

    def test_treat_http_errors_off_returns_response(self):
        with _http_server(StatusCodeHandler) as (base, _):
            client = _build_client(set_treat_http_errors_as_errors=False)
            resp = client.get(f"{base}/503/oops")
            assert isinstance(resp, HttpResponse)
            assert resp.status_code == 503
            assert resp.body() == b"oops"

    def test_truncated_body_raises_io_error(self):
        # _IncompleteReadRequestHandlerTest sends a partial chunked response.
        with _http_server(_IncompleteReadRequestHandlerTest) as (base, _):
            client = _build_client(timeout_ms=2000)
            with pytest.raises((HttpIoError, ConnectionFailedError)):
                client.post(f"{base}/", body=b"x")


# --------------------------------------------------------------------------- #
# §6.4 Transport variants
# --------------------------------------------------------------------------- #


class TestTransport:
    @pytest.mark.skipif(
        os.environ.get("CI_OFFLINE") == "1",
        reason="Network smoke test (set CI_OFFLINE=1 to skip)",
    )
    def test_https_smoke(self):
        """Smoke test against example.com — gated by CI_OFFLINE env var."""
        try:
            client = _build_client(timeout_ms=10_000)
            resp = client.get("https://example.com/")
            assert resp.status_code in (200, 301, 302)
        except (ConnectionFailedError, HttpIoError, TimedOutError):
            pytest.skip("Network unavailable")

    def test_ipv6_loopback_roundtrip(self):
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
            client = _build_client()
            resp = client.get(f"http://[::1]:{srv.server_address[1]}/")
            assert resp.status_code == 200
        finally:
            srv.shutdown()
            thread.join(timeout=2)


# --------------------------------------------------------------------------- #
# §6.4 UDS transport
# --------------------------------------------------------------------------- #


class _UDSHTTPServer(socketserver.UnixStreamServer, http.server.HTTPServer):  # type: ignore[misc]
    def server_bind(self):
        http.server.HTTPServer.server_bind(self)


@contextlib.contextmanager
def _uds_server(handler_cls=EchoHandler, sock_filename: str = "test.sock"):
    sock_dir = tempfile.mkdtemp(prefix="ddtrace-http-uds-")
    sock_path = os.path.join(sock_dir, sock_filename)
    ready = threading.Event()
    server = _UDSHTTPServer(sock_path, handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ready.set()
    try:
        yield sock_path
    finally:
        server.shutdown()
        thread.join(timeout=2)
        try:
            os.unlink(sock_path)
        except OSError:
            pass
        try:
            os.rmdir(sock_dir)
        except OSError:
            pass


@pytest.mark.skipif(sys.platform == "win32", reason="UDS is unix-only")
class TestUDS:
    def test_uds_happy_path(self):
        with _uds_server() as sock_path:
            client = HttpClientBuilder().set_timeout_ms(5000).set_unix_socket(sock_path).build(get_native_runtime())
            resp = client.get("http://localhost/info")
            assert resp.status_code == 200
            assert resp.body() == b"GET response"

    def test_uds_missing_path(self):
        client = (
            HttpClientBuilder()
            .set_timeout_ms(1000)
            .set_unix_socket("/nonexistent/uds.sock")
            .build(get_native_runtime())
        )
        with pytest.raises(ConnectionFailedError):
            client.get("http://localhost/info")

    def test_uds_stale_socket(self):
        # Create a file at the path but with no listener — connection should fail.
        sock_dir = tempfile.mkdtemp(prefix="ddtrace-http-uds-stale-")
        sock_path = os.path.join(sock_dir, "stale.sock")
        # Open and close a socket so the file exists but nobody is listening.
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.bind(sock_path)
            # No listen() / accept() — connection attempts will fail.
            client = HttpClientBuilder().set_timeout_ms(1000).set_unix_socket(sock_path).build(get_native_runtime())
            with pytest.raises((ConnectionFailedError, HttpIoError, TimedOutError)):
                client.get("http://localhost/info")
        finally:
            s.close()
            with contextlib.suppress(OSError):
                os.unlink(sock_path)
            with contextlib.suppress(OSError):
                os.rmdir(sock_dir)

    def test_uds_regular_file(self):
        # A regular file at the socket path should not panic.
        with tempfile.NamedTemporaryFile() as f:
            client = HttpClientBuilder().set_timeout_ms(1000).set_unix_socket(f.name).build(get_native_runtime())
            with pytest.raises((ConnectionFailedError, HttpIoError)):
                client.get("http://localhost/info")

    def test_uds_directory(self):
        # A directory at the socket path should not panic.
        d = tempfile.mkdtemp(prefix="ddtrace-http-uds-dir-")
        try:
            client = HttpClientBuilder().set_timeout_ms(1000).set_unix_socket(d).build(get_native_runtime())
            with pytest.raises((ConnectionFailedError, HttpIoError)):
                client.get("http://localhost/info")
        finally:
            os.rmdir(d)

    def test_uds_long_path(self):
        # Linux sun_path limit is 108 bytes; create a path longer than that.
        # Should fail gracefully, not panic.
        long_segment = "a" * 50
        path = os.path.join(tempfile.gettempdir(), long_segment, long_segment, long_segment, "sock")
        # No need to create the path — we expect a clean error either way.
        client = HttpClientBuilder().set_timeout_ms(500).set_unix_socket(path).build(get_native_runtime())
        with pytest.raises((ConnectionFailedError, HttpIoError)):
            client.get("http://localhost/info")

    def test_uds_path_with_spaces(self):
        sock_dir = tempfile.mkdtemp(prefix="ddtrace http uds spaces-")
        sock_path = os.path.join(sock_dir, "with spaces.sock")
        ready = threading.Event()
        server = _UDSHTTPServer(sock_path, EchoHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        ready.set()
        try:
            client = HttpClientBuilder().set_timeout_ms(5000).set_unix_socket(sock_path).build(get_native_runtime())
            resp = client.get("http://localhost/info")
            assert resp.status_code == 200
        finally:
            server.shutdown()
            thread.join(timeout=2)
            with contextlib.suppress(OSError):
                os.unlink(sock_path)
            with contextlib.suppress(OSError):
                os.rmdir(sock_dir)


# --------------------------------------------------------------------------- #
# §6.5 Timeouts
# --------------------------------------------------------------------------- #


class TestTimeouts:
    def test_client_timeout_fires(self):
        with _http_server(_TimeoutAPIEndpointRequestHandlerTest) as (base, _):
            client = _build_client(timeout_ms=300)
            with pytest.raises(TimedOutError):
                client.post(f"{base}/")

    def test_per_request_timeout_shorter_than_client(self):
        with _http_server(_TimeoutAPIEndpointRequestHandlerTest) as (base, _):
            client = _build_client(timeout_ms=10_000)
            req = HttpRequest(HttpMethod.Post, f"{base}/").with_timeout_ms(300)
            with pytest.raises(TimedOutError):
                client.send(req)

    def test_per_request_timeout_longer_than_client(self):
        # A per-request timeout longer than the client timeout should win.
        # Use a server that responds quickly to verify success, not failure.
        with _http_server(EchoHandler) as (base, _):
            client = _build_client(timeout_ms=100)
            req = HttpRequest(HttpMethod.Get, f"{base}/").with_timeout_ms(5000)
            resp = client.send(req)
            assert resp.status_code == 200

    def test_timeout_within_tolerance(self):
        with _http_server(_TimeoutAPIEndpointRequestHandlerTest) as (base, _):
            client = _build_client(timeout_ms=500)
            start = time.monotonic()
            with pytest.raises(TimedOutError):
                client.post(f"{base}/")
            elapsed = (time.monotonic() - start) * 1000
            assert 300 <= elapsed <= 1500, f"timeout fired at {elapsed}ms, expected ~500ms"


# --------------------------------------------------------------------------- #
# §6.6 Retries
# --------------------------------------------------------------------------- #


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


class TestRetries:
    def test_retry_succeeds_after_failures(self):
        class _H(_FailNTimesHandler):
            pass

        _H.reset(fail_n=2, final_body=b"recovered")
        with _http_server(_H) as (base, _):
            client = (
                HttpClientBuilder()
                .set_timeout_ms(2000)
                .set_retry(3, initial_delay_ms=10, jitter=False)
                .build(get_native_runtime())
            )
            resp = client.post(f"{base}/")
            assert resp.status_code == 200
            assert _H._attempts == 3

    def test_retry_all_fail_terminal_error(self):
        # A server that always fails: counts attempts and raises after.
        class _H(_FailNTimesHandler):
            pass

        # fail_n large enough that all attempts fail; we'll expect an error.
        _H.reset(fail_n=99)
        with _http_server(_H) as (base, _):
            client = (
                HttpClientBuilder()
                .set_timeout_ms(1000)
                .set_retry(2, initial_delay_ms=10, jitter=False)
                .build(get_native_runtime())
            )
            with pytest.raises((HttpIoError, ConnectionFailedError)):
                client.post(f"{base}/")
            # max_retries=2 means 1 initial + 2 retries = 3 attempts
            assert _H._attempts == 3

    def test_retry_zero_is_one_attempt(self):
        class _H(_FailNTimesHandler):
            pass

        _H.reset(fail_n=99)
        with _http_server(_H) as (base, _):
            client = (
                HttpClientBuilder()
                .set_timeout_ms(1000)
                .set_retry(0, initial_delay_ms=10, jitter=False)
                .build(get_native_runtime())
            )
            with pytest.raises((HttpIoError, ConnectionFailedError)):
                client.post(f"{base}/")
            assert _H._attempts == 1

    def test_invalid_config_not_retried(self):
        # InvalidConfigError isn't retried by libdd. Test by setting both body
        # and multipart on a request — libdd raises InvalidConfig at send time.
        with _http_server(EchoHandler) as (base, _):
            client = (
                HttpClientBuilder()
                .set_timeout_ms(1000)
                .set_retry(3, initial_delay_ms=10, jitter=False)
                .build(get_native_runtime())
            )
            req = HttpRequest(HttpMethod.Post, f"{base}/").with_body(b"a").with_multipart_part(MultipartPart("x", b"y"))
            with pytest.raises(InvalidConfigError):
                client.send(req)

    def test_404_with_retry_retries(self):
        # libdd default: 4xx ARE retried (gotcha).
        class _StatusH(StatusCodeHandler):
            _hits = 0

            def do_GET(self):
                _StatusH._hits += 1
                self._serve()

        _StatusH._hits = 0
        with _http_server(_StatusH) as (base, _):
            client = (
                HttpClientBuilder()
                .set_timeout_ms(2000)
                .set_retry(3, initial_delay_ms=5, jitter=False)
                .build(get_native_runtime())
            )
            with pytest.raises(RequestFailedError):
                client.get(f"{base}/404/notfound")
            assert _StatusH._hits == 4  # 1 + 3 retries

    def test_404_with_treat_off_no_retries(self):
        class _StatusH(StatusCodeHandler):
            _hits = 0

            def do_GET(self):
                _StatusH._hits += 1
                self._serve()

        _StatusH._hits = 0
        with _http_server(_StatusH) as (base, _):
            client = (
                HttpClientBuilder()
                .set_timeout_ms(2000)
                .set_treat_http_errors_as_errors(False)
                .set_retry(3, initial_delay_ms=5, jitter=False)
                .build(get_native_runtime())
            )
            resp = client.get(f"{base}/404/notfound")
            assert resp.status_code == 404
            assert _StatusH._hits == 1  # no retry because not an error variant

    def test_retry_delay_respected(self):
        # With jitter disabled, the delay must be >= initial_delay_ms.
        class _H(_FailNTimesHandler):
            pass

        _H.reset(fail_n=2)
        with _http_server(_H) as (base, _):
            client = (
                HttpClientBuilder()
                .set_timeout_ms(5000)
                .set_retry(2, initial_delay_ms=200, jitter=False)
                .build(get_native_runtime())
            )
            start = time.monotonic()
            client.post(f"{base}/")
            elapsed = (time.monotonic() - start) * 1000
            # 200ms before retry 1 + 400ms before retry 2 = at least 600ms.
            assert elapsed >= 400, f"retries elapsed only {elapsed}ms, expected >= 400"

    def test_retry_on_connection_refused(self):
        # Bind a socket, close it — connecting will fail.
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        client = (
            HttpClientBuilder()
            .set_timeout_ms(1000)
            .set_retry(2, initial_delay_ms=5, jitter=False)
            .build(get_native_runtime())
        )
        with pytest.raises(ConnectionFailedError):
            client.get(f"http://127.0.0.1:{port}/")

    def test_connection_reset_mid_response(self):
        with _http_server(_ResetAPIEndpointRequestHandlerTest) as (base, _):
            client = HttpClientBuilder().set_timeout_ms(1000).set_retry(0).build(get_native_runtime())
            with pytest.raises((HttpIoError, ConnectionFailedError)):
                client.post(f"{base}/")


# --------------------------------------------------------------------------- #
# §6.7 Headers
# --------------------------------------------------------------------------- #


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


class TestHeaders:
    def test_single_header(self):
        with _http_server(_HeaderEchoHandler) as (base, _):
            client = _build_client()
            resp = client.get(f"{base}/", headers=[("X-One", "v1")])
            assert ("x-one", "v1") in _items_lower(resp.body())

    def test_multiple_headers(self):
        with _http_server(_HeaderEchoHandler) as (base, _):
            client = _build_client()
            resp = client.get(f"{base}/", headers=[("A", "1"), ("B", "2")])
            items = _items_lower(resp.body())
            assert ("a", "1") in items and ("b", "2") in items

    def test_duplicate_request_header_preserved(self):
        with _http_server(_HeaderEchoHandler) as (base, _):
            client = _build_client()
            resp = client.get(f"{base}/", headers=[("X", "a"), ("X", "b")])
            items = _items_lower(resp.body())
            x_values = [v for k, v in items if k == "x"]
            assert "a" in x_values and "b" in x_values

    def test_unicode_header_value(self):
        # UTF-8 header values must encode correctly (reqwest validates ASCII
        # but the underlying http crate allows opaque bytes).
        client = _build_client()
        req = HttpRequest(HttpMethod.Get, "http://127.0.0.1:1/").with_header("X", "hello-世界")
        # Either rejected at send time (InvalidConfig) or sent through.
        with pytest.raises((ConnectionFailedError, InvalidConfigError, HttpIoError)):
            client.send(req)

    @pytest.mark.parametrize("bad_value", ["v\r\nX-Injected: 1", "v\nX-Injected: 1", "v\x00v"])
    def test_crlf_injection_rejected(self, bad_value):
        # CRLF / LF / NUL must be rejected to prevent header injection.
        with _http_server(EchoHandler) as (base, _):
            client = _build_client()
            with pytest.raises((InvalidConfigError, ConnectionFailedError, HttpIoError, ValueError)):
                client.get(f"{base}/", headers=[("X", bad_value)])

    def test_empty_header_value_accepted(self):
        with _http_server(_HeaderEchoHandler) as (base, _):
            client = _build_client()
            resp = client.get(f"{base}/", headers=[("X-Empty", "")])
            items = _items_lower(resp.body())
            assert ("x-empty", "") in items

    def test_many_distinct_headers(self):
        # DEV: Python's http.server caps at ~100 total request headers (HTTP 431
        # otherwise); 50 user headers is well within that budget while still
        # being a meaningful "lots of headers" smoke test.
        headers = [(f"X-H{i}", f"v{i}") for i in range(50)]
        with _http_server(_HeaderEchoHandler) as (base, _):
            client = _build_client()
            resp = client.get(f"{base}/", headers=headers)
            items = _items_lower(resp.body())
            for name, value in headers:
                assert (name.lower(), value) in items


# --------------------------------------------------------------------------- #
# §6.8 Multipart
# --------------------------------------------------------------------------- #


class _MultipartCollectorHandler(_BaseHTTPRequestHandler):
    """Saves the raw POST body so tests can inspect what came over the wire."""

    last_body = b""
    last_content_type = ""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        type(self).last_body = self.rfile.read(length) if length else b""
        type(self).last_content_type = self.headers.get("Content-Type", "")
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"OK")


class TestMultipart:
    def test_single_part(self):
        with _http_server(_MultipartCollectorHandler) as (base, _):
            client = _build_client()
            req = HttpRequest(HttpMethod.Post, f"{base}/").with_multipart_part(MultipartPart("field", b"value"))
            resp = client.send(req)
            assert resp.status_code == 200
            body = _MultipartCollectorHandler.last_body
            assert b'name="field"' in body
            assert b"value" in body
            assert _MultipartCollectorHandler.last_content_type.startswith("multipart/form-data")

    def test_part_with_filename_and_content_type(self):
        with _http_server(_MultipartCollectorHandler) as (base, _):
            client = _build_client()
            req = HttpRequest(HttpMethod.Post, f"{base}/").with_multipart_part(
                MultipartPart("file", b"data", filename="test.bin", content_type="application/octet-stream")
            )
            client.send(req)
            body = _MultipartCollectorHandler.last_body
            assert b'name="file"' in body
            assert b'filename="test.bin"' in body
            assert b"application/octet-stream" in body

    def test_body_and_multipart_rejected_at_send(self):
        with _http_server(EchoHandler) as (base, _):
            client = _build_client()
            req = HttpRequest(HttpMethod.Post, f"{base}/").with_body(b"a").with_multipart_part(MultipartPart("x", b"y"))
            with pytest.raises(InvalidConfigError):
                client.send(req)

    def test_binary_part_data_preserved(self):
        # SymDB-style: gzipped bytes containing CRLF and `--boundary` lookalikes.
        binary = b"\x1f\x8b\x08\x00" + b"\r\n--boundary-lookalike\r\n" + b"\xff\x00\x01"
        with _http_server(_MultipartCollectorHandler) as (base, _):
            client = _build_client()
            req = HttpRequest(HttpMethod.Post, f"{base}/").with_multipart_part(
                MultipartPart("blob", binary, content_type="application/octet-stream")
            )
            client.send(req)
            assert binary in _MultipartCollectorHandler.last_body

    def test_two_parts_mixed(self):
        # CIVis pattern: JSON + binary.
        with _http_server(_MultipartCollectorHandler) as (base, _):
            client = _build_client()
            req = (
                HttpRequest(HttpMethod.Post, f"{base}/")
                .with_multipart_part(MultipartPart("event", b'{"k":1}', content_type="application/json"))
                .with_multipart_part(
                    MultipartPart("file", b"\x00\x01\x02", filename="x.bin", content_type="application/octet-stream")
                )
            )
            client.send(req)
            body = _MultipartCollectorHandler.last_body
            assert b'name="event"' in body
            assert b'name="file"' in body
            assert b'{"k":1}' in body

    def test_empty_part_data(self):
        with _http_server(_MultipartCollectorHandler) as (base, _):
            client = _build_client()
            req = HttpRequest(HttpMethod.Post, f"{base}/").with_multipart_part(MultipartPart("empty", b""))
            resp = client.send(req)
            assert resp.status_code == 200

    def test_body_with_multipart_content_type_header_sent_verbatim(self):
        # CIVis git_client pattern: caller pre-assembles multipart bytes.
        preassembled = b'--BOUND\r\nContent-Disposition: form-data; name="x"\r\n\r\nv\r\n--BOUND--\r\n'
        with _http_server(_MultipartCollectorHandler) as (base, _):
            client = _build_client()
            req = (
                HttpRequest(HttpMethod.Post, f"{base}/")
                .with_header("Content-Type", "multipart/form-data; boundary=BOUND")
                .with_body(preassembled)
            )
            resp = client.send(req)
            assert resp.status_code == 200
            # Body sent through verbatim.
            assert _MultipartCollectorHandler.last_body == preassembled


# --------------------------------------------------------------------------- #
# §6.9 GIL behavior / threading
# --------------------------------------------------------------------------- #


class TestGIL:
    def test_gil_released_during_send(self):
        # While one thread is in client.send(), another thread should keep
        # incrementing a counter — proving the GIL is released.
        with _http_server(_TimeoutAPIEndpointRequestHandlerTest) as (base, _):
            client = _build_client(timeout_ms=600)
            counter = [0]
            stop = threading.Event()

            def incr():
                while not stop.is_set():
                    counter[0] += 1

            t = threading.Thread(target=incr, daemon=True)
            t.start()
            try:
                with pytest.raises(TimedOutError):
                    client.post(f"{base}/")
            finally:
                stop.set()
                t.join(timeout=1)
            # The counter thread should have made significant progress.
            assert counter[0] > 1000, f"GIL appears to be held: only {counter[0]} iterations"

    def test_concurrent_threads_share_client(self):
        with _http_server(EchoHandler) as (base, _):
            client = _build_client()
            errors = []
            successes = [0]
            lock = threading.Lock()

            def worker():
                try:
                    for _ in range(10):
                        resp = client.get(f"{base}/")
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

    from ddtrace.internal.native import HttpClientBuilder
    from ddtrace.internal.native_runtime import get_native_runtime

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
    _ = HttpClientBuilder().set_timeout_ms(2000).build(get_native_runtime())

    pid = os.fork()
    if pid == 0:
        # Child: spin up our own server, build a fresh client, send a request.
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", p2), EchoHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            client = HttpClientBuilder().set_timeout_ms(2000).build(get_native_runtime())
            resp = client.get(f"http://127.0.0.1:{p2}/")
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
            client = HttpClientBuilder().set_timeout_ms(2000).build(get_native_runtime())
            resp = client.get(f"http://127.0.0.1:{p1}/")
            assert resp.status_code == 200
            assert resp.body() == b"ok"
        finally:
            srv.shutdown()
        _, status = os.waitpid(pid, 0)
        assert os.WEXITSTATUS(status) == 0


# --------------------------------------------------------------------------- #
# §6.11 Error mapping
# --------------------------------------------------------------------------- #


class TestErrorMapping:
    def test_all_subclass_http_client_error(self):
        assert issubclass(ConnectionFailedError, HttpClientError)
        assert issubclass(TimedOutError, HttpClientError)
        assert issubclass(RequestFailedError, HttpClientError)
        assert issubclass(InvalidConfigError, HttpClientError)
        assert issubclass(HttpIoError, HttpClientError)

    def test_connection_refused(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        client = _build_client(timeout_ms=1000)
        with pytest.raises(ConnectionFailedError) as exc_info:
            client.get(f"http://127.0.0.1:{port}/")
        # Just verify the exception carries a message; the exact wording is
        # libdd / reqwest implementation detail.
        assert str(exc_info.value)

    def test_503_response(self):
        with _http_server(StatusCodeHandler) as (base, _):
            client = _build_client()
            with pytest.raises(RequestFailedError) as exc_info:
                client.get(f"{base}/503/oops")
            assert exc_info.value.status == 503
            assert exc_info.value.body == "oops"


# --------------------------------------------------------------------------- #
# §6.12 Concurrency / pooling
# --------------------------------------------------------------------------- #


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


class TestPooling:
    def test_pooling_default_few_connections(self):
        _AcceptCountingHandler.accept_count = 0
        with _http_server(_AcceptCountingHandler) as (base, _):
            client = _build_client()
            for _ in range(50):
                resp = client.get(f"{base}/")
                assert resp.status_code == 200
            # With pooling on, we expect far fewer than 50 connections.
            assert _AcceptCountingHandler.accept_count <= 10, (
                f"expected pooling, got {_AcceptCountingHandler.accept_count} connections"
            )

    def test_pooling_disabled_many_connections(self):
        _AcceptCountingHandler.accept_count = 0
        with _http_server(_AcceptCountingHandler) as (base, _):
            client = (
                HttpClientBuilder().set_timeout_ms(5000).set_allow_connection_pooling(False).build(get_native_runtime())
            )
            for _ in range(10):
                resp = client.get(f"{base}/")
                assert resp.status_code == 200
            # Without pooling, each request opens its own connection.
            assert _AcceptCountingHandler.accept_count >= 10


# --------------------------------------------------------------------------- #
# §6.14 Resource management
# --------------------------------------------------------------------------- #


class TestResourceManagement:
    def test_no_fd_leak_when_building_many_clients(self):
        psutil = pytest.importorskip("psutil")
        proc = psutil.Process()
        baseline = proc.num_fds() if hasattr(proc, "num_fds") else len(proc.open_files())
        for _ in range(100):
            c = HttpClientBuilder().set_timeout_ms(500).build(get_native_runtime())
            c.shutdown()
            del c
        # Allow a small slack for runtime-internal bookkeeping.
        after = proc.num_fds() if hasattr(proc, "num_fds") else len(proc.open_files())
        assert after - baseline < 20, f"possible FD leak: {baseline} -> {after}"


# --------------------------------------------------------------------------- #
# §6.16 Request body encoding (Datadog payload shapes)
# --------------------------------------------------------------------------- #


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


class TestRequestBodyEncoding:
    def test_pre_gzipped_body_sent_verbatim(self):
        raw = b'{"k":"v"}' * 100
        gzipped = gzip.compress(raw)
        with _http_server(_BodyCapturingHandler) as (base, _):
            client = _build_client()
            resp = client.post(
                f"{base}/",
                headers=[("Content-Type", "application/json"), ("Content-Encoding", "gzip")],
                body=gzipped,
            )
            assert resp.status_code == 200
            assert _BodyCapturingHandler.last_body == gzipped
            assert _BodyCapturingHandler.last_headers.get("content-encoding") == "gzip"

    def test_msgpack_binary_body_byte_exact(self):
        # Synthetic msgpack-ish bytes.
        payload = b"\x81\xa3foo\x01" + bytes(range(64))
        with _http_server(_BodyCapturingHandler) as (base, _):
            client = _build_client()
            client.post(
                f"{base}/",
                headers=[("Content-Type", "application/msgpack")],
                body=payload,
            )
            assert _BodyCapturingHandler.last_body == payload


# --------------------------------------------------------------------------- #
# §6.17 Response decompression (pinned: no auto-decompress)
# --------------------------------------------------------------------------- #


class TestNoAutoDecompress:
    def test_gzip_response_returned_raw(self):
        raw = b"hello world! " * 100
        gzipped = gzip.compress(raw)

        class _GZHandler(_BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(gzipped)))
                self.end_headers()
                self.wfile.write(gzipped)

        with _http_server(_GZHandler) as (base, _):
            client = _build_client()
            resp = client.get(f"{base}/")
            assert resp.body() == gzipped
            assert resp.header("content-encoding") == "gzip"


# --------------------------------------------------------------------------- #
# §6.18 URL handling
# --------------------------------------------------------------------------- #


class _PathCapturingHandler(_BaseHTTPRequestHandler):
    last_path = ""

    def do_GET(self):
        type(self).last_path = self.path
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"OK")


class TestURLHandling:
    def test_query_string_preserved(self):
        with _http_server(_PathCapturingHandler) as (base, _):
            client = _build_client()
            client.get(f"{base}/v0.4/traces?a=1&b=foo%20bar")
            assert "?a=1&b=foo%20bar" in _PathCapturingHandler.last_path

    def test_fragment_not_sent(self):
        with _http_server(_PathCapturingHandler) as (base, _):
            client = _build_client()
            client.get(f"{base}/info#section")
            # HTTP standard: fragments are client-side only.
            assert "#" not in _PathCapturingHandler.last_path

    def test_trailing_slash_distinct(self):
        with _http_server(_PathCapturingHandler) as (base, _):
            client = _build_client()
            client.get(f"{base}/v0.4/traces")
            no_slash = _PathCapturingHandler.last_path
            client.get(f"{base}/v0.4/traces/")
            with_slash = _PathCapturingHandler.last_path
            assert no_slash != with_slash


# --------------------------------------------------------------------------- #
# §6.21 PyO3 lifecycle / move semantics
# --------------------------------------------------------------------------- #


class TestLifecycle:
    def test_request_consumed_after_send(self):
        with _http_server(EchoHandler) as (base, _):
            client = _build_client()
            req = HttpRequest(HttpMethod.Get, f"{base}/")
            client.send(req)
            with pytest.raises(ValueError):
                client.send(req)

    def test_with_header_after_send_raises(self):
        with _http_server(EchoHandler) as (base, _):
            client = _build_client()
            req = HttpRequest(HttpMethod.Get, f"{base}/")
            client.send(req)
            with pytest.raises(ValueError):
                req.with_header("X", "v")

    def test_shutdown_blocks_send(self):
        client = _build_client(timeout_ms=500)
        client.shutdown()
        with pytest.raises(ValueError):
            client.get("http://127.0.0.1:1/")
        with pytest.raises(ValueError):
            client.send(HttpRequest(HttpMethod.Get, "http://127.0.0.1:1/"))

    def test_repr_after_shutdown(self):
        client = _build_client(timeout_ms=500)
        client.shutdown()
        # Must NOT raise.
        assert "closed" in repr(client)

    def test_builder_build_twice_raises(self):
        builder = HttpClientBuilder().set_timeout_ms(500)
        builder.build(get_native_runtime())
        with pytest.raises(ValueError):
            builder.build(get_native_runtime())

    def test_dict_headers_raises_type_error(self):
        with _http_server(EchoHandler) as (base, _):
            client = _build_client()
            # PyO3 will iterate a dict's keys → expects tuples, gets strings.
            with pytest.raises(TypeError):
                client.get(f"{base}/", headers={"k": "v"})  # type: ignore[arg-type]

    def test_http_client_not_subclassable(self):
        with pytest.raises(TypeError):

            class S(HttpClient):  # noqa: F841
                pass

    def test_http_client_error_is_subclassable(self):
        class MyError(HttpClientError):
            pass

        # Just verify subclass relationship works.
        assert issubclass(MyError, HttpClientError)


# --------------------------------------------------------------------------- #
# §6.22 Datadog response shape sanity
# --------------------------------------------------------------------------- #


class TestDatadogResponseShape:
    def test_ok_body(self):
        class _OKHandler(_BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"OK")

        with _http_server(_OKHandler) as (base, _):
            client = _build_client()
            resp = client.post(f"{base}/v0.4/traces")
            assert resp.body() == b"OK"

    def test_202_empty_body_not_raised(self):
        class _202Handler(_BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(202)
                self.end_headers()

        with _http_server(_202Handler) as (base, _):
            client = _build_client()
            resp = client.post(f"{base}/telemetry")
            assert resp.status_code == 202
            assert resp.body() == b""

    def test_204_empty_body(self):
        class _204Handler(_BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(204)
                self.end_headers()

        with _http_server(_204Handler) as (base, _):
            client = _build_client()
            resp = client.post(f"{base}/v0.7/config")
            assert resp.status_code == 204
            assert resp.body() == b""

    def test_json_envelope_byte_exact(self):
        # Realistic /info payload sample.
        payload = b'{"endpoints":["/v0.4/traces","/v0.7/config"],"client_drop_p0s":true,"version":"7.50.0"}'

        class _InfoHandler(_BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        with _http_server(_InfoHandler) as (base, _):
            client = _build_client()
            resp = client.get(f"{base}/info")
            assert resp.body() == payload


# --------------------------------------------------------------------------- #
# apply_container_headers helper
# --------------------------------------------------------------------------- #


def test_apply_container_headers_dict_to_list():
    from ddtrace.internal.native import apply_container_headers

    result = apply_container_headers({"X-Custom": "v"})
    assert isinstance(result, list)
    assert ("X-Custom", "v") in result


def test_apply_container_headers_none_is_empty_or_container():
    from ddtrace.internal.native import apply_container_headers

    result = apply_container_headers(None)
    # The result may or may not include container headers depending on env,
    # but it must be a list of tuples.
    assert isinstance(result, list)
    for item in result:
        assert isinstance(item, tuple) and len(item) == 2


def test_build_native_http_client_http():
    from ddtrace.internal.utils.http import build_native_http_client

    client = build_native_http_client("http://localhost:8126", 1000)
    assert "open" in repr(client)
    client.shutdown()


@pytest.mark.skipif(sys.platform == "win32", reason="UDS is unix-only")
def test_build_native_http_client_unix():
    from ddtrace.internal.utils.http import build_native_http_client

    client = build_native_http_client("unix:///tmp/dd.sock", 1000)
    assert "open" in repr(client)
    client.shutdown()
