import contextlib
from unittest import mock

import pytest

from ddtrace.appsec._common_module_patches import patch_common_modules
from ddtrace.appsec._common_module_patches import unpatch_common_modules
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._contrib.httplib import listen as httplib_listen
from ddtrace.appsec._contrib.httplib import subscribers as httplib_appsec
from ddtrace.appsec._contrib.httplib import unlisten as httplib_unlisten
from ddtrace.contrib._events.http_client import HttpClientEvents
from ddtrace.contrib.internal.httplib.events import HttpClientRequestContext
from ddtrace.contrib.internal.httplib.events import HttpClientResponseContext
from ddtrace.contrib.internal.httplib.events import unpatch_http_client_events
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.wrapping.context import WrappingContext
from ddtrace.internal.wrapping.hooks import _WRAPPING_CONTEXTS
from tests.appsec.utils import blocking_waf_result


@pytest.fixture(autouse=True)
def httplib_subscriber():
    was_active = core.has_listeners("context.started." + HttpClientEvents.HTTPLIB_REQUEST.value)
    httplib_listen()
    try:
        yield
    finally:
        httplib_unlisten()
        unpatch_http_client_events()
        if was_active:
            httplib_listen()


@contextlib.contextmanager
def _downstream_url(full_url):
    """Publish full_url in a thread-local core context, the way the outer client hooks do.

    The root context is the ContextVar default, so a URL published there is visible to ddtrace's
    own writer threads, whose flush requests would consume the RASP decision meant for the test.
    """
    with core.context_with_data("test.downstream_request"):
        core.set_item("full_url", full_url)
        yield


def test_http_connection_request_blocks_on_a_waf_block_decision():
    """A SSRF_REQ block must propagate before the native method connects."""
    import http.client

    from ddtrace.contrib.internal.httplib.patch import unpatch as httplib_unpatch

    httplib_unpatch()
    with (
        _downstream_url("http://127.0.0.1:1/"),
        mock.patch.object(httplib_appsec, "get_rasp_capability", return_value=True),
        mock.patch.object(httplib_appsec, "get_active_asm_context", return_value=mock.Mock(downstream_requests=0)),
        mock.patch.object(httplib_appsec, "call_waf_callback", return_value=blocking_waf_result()) as waf,
        mock.patch.object(httplib_appsec, "get_blocked", return_value={"status_code": 403}),
        mock.patch.object(http.client.HTTPConnection, "connect") as transport,
    ):
        conn = http.client.HTTPConnection("127.0.0.1", 1, timeout=1)
        with pytest.raises(BlockingException) as raised:
            conn.request("GET", "/")
        transport.assert_not_called()
    assert raised.value.args[3] == "http://127.0.0.1:1/"
    assert waf.call_args.kwargs["crop_trace"] == "request"


def test_a_blocked_request_leaves_no_wrapping_storage_behind():
    """Failed entry must release both the producer's and universal context's storage."""
    import http.client

    from ddtrace.contrib.internal.httplib.patch import unpatch as httplib_unpatch
    from ddtrace.internal.wrapping.context import _UniversalWrappingContext

    httplib_unpatch()
    concrete = _WRAPPING_CONTEXTS[("http.client", "HTTPConnection.request")]
    universal = _UniversalWrappingContext.extract(concrete.__wrapped__)
    for _ in range(3):
        with (
            _downstream_url("http://127.0.0.1:1/"),
            mock.patch.object(httplib_appsec, "get_rasp_capability", return_value=True),
            mock.patch.object(httplib_appsec, "get_active_asm_context", return_value=mock.Mock(downstream_requests=0)),
            mock.patch.object(httplib_appsec, "call_waf_callback", return_value=blocking_waf_result()),
            mock.patch.object(httplib_appsec, "get_blocked", return_value={"status_code": 403}),
        ):
            conn = http.client.HTTPConnection("127.0.0.1", 1, timeout=1)
            with pytest.raises(BlockingException):
                conn.request("GET", "/")
    assert concrete._storage.get() is None
    assert universal._storage.get() is None


def test_a_concurrent_request_does_not_consume_the_pending_block():
    """A writer thread cannot inherit the application's downstream URL or ASM environment."""
    import http.client
    import threading

    from ddtrace.contrib.internal.httplib.patch import unpatch as httplib_unpatch

    httplib_unpatch()
    env = mock.Mock(downstream_requests=0)
    with (
        _downstream_url("http://127.0.0.1:1/"),
        mock.patch.object(httplib_appsec, "get_rasp_capability", return_value=True),
        mock.patch.object(
            httplib_appsec, "get_active_asm_context", side_effect=lambda: env if core.find_item("full_url") else None
        ),
        mock.patch.object(httplib_appsec, "call_waf_callback", return_value=blocking_waf_result()) as waf,
        mock.patch.object(httplib_appsec, "get_blocked", return_value={"status_code": 403}),
    ):

        def flush():
            with contextlib.suppress(OSError):
                http.client.HTTPConnection("127.0.0.1", 1, timeout=1).request("GET", "/flush")

        thread = threading.Thread(target=flush)
        thread.start()
        thread.join()
        conn = http.client.HTTPConnection("127.0.0.1", 1, timeout=1)
        with pytest.raises(BlockingException):
            conn.request("GET", "/")
    waf.assert_called_once()


def test_http_client_request_traceback_has_no_ddtrace_frames():
    """Neutral bytecode observation preserves the application's original exception traceback."""
    import http.client
    import traceback

    from ddtrace.contrib.internal.httplib.patch import unpatch as httplib_unpatch

    httplib_unpatch()
    conn = http.client.HTTPConnection("127.0.0.1", 1, timeout=1)
    with pytest.raises(OSError) as raised:
        conn.request("GET", "/")
    frames = traceback.extract_tb(raised.value.__traceback__)
    assert not [frame.filename for frame in frames if "ddtrace" in frame.filename]
    assert any(frame.filename == __file__ for frame in frames)


@pytest.mark.parametrize("appsec_first", [True, False])
def test_http_client_context_coexists_with_httplib_contrib(appsec_first):
    """Neutral bytecode observation and optional httplib spans survive either installation order."""
    httplib_unlisten()
    unpatch_http_client_events()
    import http.client

    from ddtrace.contrib.internal.httplib.patch import patch as httplib_patch
    from ddtrace.contrib.internal.httplib.patch import unpatch as httplib_unpatch

    httplib_unpatch()
    try:
        if appsec_first:
            httplib_listen()
            httplib_patch()
        else:
            httplib_patch()
            httplib_listen()

        request_ctx = _WRAPPING_CONTEXTS.get(("http.client", "HTTPConnection.request"))
        assert isinstance(request_ctx, HttpClientRequestContext)
        assert isinstance(
            _WRAPPING_CONTEXTS.get(("http.client", "HTTPConnection.getresponse")),
            HttpClientResponseContext,
        )

        # Being registered is not enough: the hook must actually run under both orders, or
        # RASP silently stops inspecting downstream requests.
        entered = []

        class _Probe(HttpClientRequestContext):
            def __enter__(self):
                result = super().__enter__()
                frame_locals = self.__frame__.f_locals
                entered.append((frame_locals.get("method"), frame_locals.get("url")))
                return result

        request_fn = request_ctx.__wrapped__
        request_ctx.unwrap()
        probe = _Probe(request_fn)
        probe.wrap()

        # Both layers still deliver a working client: a refused connection must surface as
        # OSError, not as an instrumentation error.
        conn = http.client.HTTPConnection("127.0.0.1", 1, timeout=1)
        with pytest.raises(OSError):
            conn.request("GET", "/")

        assert entered == [("GET", "/")]
    finally:
        with contextlib.suppress(Exception):
            probe.unwrap()
        httplib_unpatch()
        httplib_unlisten()
        unpatch_http_client_events()


def _code_sizes():
    import http.client
    import urllib.request

    return {
        "request": len(http.client.HTTPConnection.__dict__["request"].__code__.co_code),
        "getresponse": len(http.client.HTTPConnection.__dict__["getresponse"].__code__.co_code),
        "open": len(urllib.request.OpenerDirector.__dict__["open"].__code__.co_code),
    }


def test_context_unpatch_restores_the_original_bytecode():
    """Unpatching must really release the function, whatever else is wrapped on the attribute.

    Unwrapping a contrib proxy instead no-ops, so each cycle rewrites on top of the last until the
    bytecode library cannot parse it - which surfaces as a KeyError in some unrelated test.
    """
    from ddtrace.contrib.internal.httplib.patch import patch as httplib_patch
    from ddtrace.contrib.internal.httplib.patch import unpatch as httplib_unpatch

    httplib_unlisten()
    unpatch_http_client_events()
    httplib_unpatch()
    unpatch_common_modules()
    baseline = _code_sizes()

    try:
        for cycle in range(6):
            # Alternate the order so both interleavings with the httplib integration are covered.
            if cycle % 2:
                httplib_patch()
                httplib_listen()
            else:
                httplib_listen()
                httplib_patch()
            patch_common_modules()
            httplib_unpatch()
            httplib_unlisten()
            unpatch_http_client_events()
            unpatch_common_modules()
            assert _code_sizes() == baseline, f"bytecode grew on cycle {cycle}"
    finally:
        httplib_unpatch()
        httplib_unlisten()
        unpatch_http_client_events()


def test_the_getresponse_context_releases_storage_when_rasp_is_off():
    """The early return for the disabled path must still chain to super().

    Returning the value directly instead would chain one storage dict per call onto the context
    variable for the lifetime of the thread, which is what #20069 fixed in the selenium context.
    """
    import http.client

    context = HttpClientResponseContext(http.client.HTTPConnection.getresponse)
    WrappingContext.__enter__(context)
    assert context._storage.get() is not None

    with mock.patch.object(httplib_appsec, "get_rasp_capability", return_value=False):
        context.__return__(object())

    assert context._storage.get() is None


@pytest.mark.parametrize(
    "path",
    [
        "/real/path?q=1",
        # A "://" in the query must not be mistaken for an absolute URL.
        "/real/path?next=http://example.test/",
    ],
)
def test_downstream_ssrf_address_keeps_the_host_under_the_httplib_contrib(path):
    """A relative request target must retain its connection's host in the WAF address."""
    import http.client

    from ddtrace.contrib.internal.httplib.patch import patch as httplib_patch
    from ddtrace.contrib.internal.httplib.patch import unpatch as httplib_unpatch

    httplib_patch()
    try:
        with (
            mock.patch.object(httplib_appsec, "get_rasp_capability", return_value=True),
            mock.patch.object(httplib_appsec, "get_active_asm_context", return_value=mock.Mock(downstream_requests=0)),
            mock.patch.object(httplib_appsec, "should_analyze_body_response", return_value=False),
            mock.patch.object(httplib_appsec, "call_waf_callback") as waf,
        ):
            conn = http.client.HTTPConnection("127.0.0.1", 1, timeout=1)
            with pytest.raises(OSError):
                conn.request("GET", path)
        waf.assert_called_once()
        assert waf.call_args.args[0][EXPLOIT_PREVENTION.ADDRESS.SSRF] == f"http://127.0.0.1:1{path}"
    finally:
        httplib_unpatch()


@pytest.mark.parametrize(
    "host, port, secure, expected",
    [
        ("127.0.0.1", 8080, False, "http://127.0.0.1:8080/p?q=1"),
        ("example.com", None, False, "http://example.com/p?q=1"),
        ("example.com", None, True, "https://example.com/p?q=1"),
        # http.client stores IPv6 literals unbracketed, and an unbracketed authority does not parse.
        ("::1", 8080, False, "http://[::1]:8080/p?q=1"),
        ("[::1]", None, False, "http://[::1]/p?q=1"),
        # A bare IPv6 literal with no port must be bracketed by the caller: http.client itself
        # parses "::1" as host ":" port 1, long before we see it.
        ("[::1]", None, True, "https://[::1]/p?q=1"),
    ],
)
def test_absolute_downstream_url(host, port, secure, expected):
    import http.client
    from urllib.parse import urlparse

    connection_cls = http.client.HTTPSConnection if secure else http.client.HTTPConnection
    connection = connection_cls(host, port) if port is not None else connection_cls(host)

    url = httplib_appsec._absolute_downstream_url(connection, "/p?q=1")

    assert url == expected
    # The point of the address is the host, so it has to survive parsing.
    assert urlparse(url).hostname == host.strip("[]")


@pytest.mark.parametrize(
    "tunnel_host, tunnel_port, expected",
    [
        ("127.0.0.1", 8443, "https://127.0.0.1:8443/p?q=1"),
        ("dest.test", None, "https://dest.test/p?q=1"),
    ],
)
def test_absolute_downstream_url_follows_a_connect_tunnel(tunnel_host, tunnel_port, expected):
    """host/port hold the proxy for a tunnelled request; the destination is the tunnel target."""
    import http.client

    connection = http.client.HTTPSConnection("proxy.example", 8080)
    if tunnel_port is None:
        connection.set_tunnel(tunnel_host)
    else:
        connection.set_tunnel(tunnel_host, tunnel_port)

    assert httplib_appsec._absolute_downstream_url(connection, "/p?q=1") == expected


@pytest.mark.parametrize(
    "url, carries_host",
    [
        ("http://127.0.0.1:1/real/path?q=1", True),
        ("//example.test/path", True),
        ("/path", False),
        # The shape of an SSRF payload: a substring test for "://" would call this absolute and
        # skip host reconstruction on exactly the requests worth inspecting.
        ("/path?next=http://example.test/", False),
        ("/redir?u=https://evil.test/x&v=http://b.test", False),
    ],
)
def test_carries_a_host(url, carries_host):
    assert httplib_appsec._carries_a_host(url) is carries_host


class _FakeSocket:
    """Accepts whatever http.client sends and replays one canned HTTP response."""

    def __init__(self, raw_response):
        self._raw_response = raw_response

    def sendall(self, data):
        pass

    def makefile(self, mode, *args, **kwargs):
        import io

        return io.BytesIO(self._raw_response)

    def close(self):
        pass


def _connection(
    raw_response=b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 11\r\n\r\n{"a": true}',
):
    import http.client

    connection = http.client.HTTPConnection("downstream.test", 8080, timeout=1)
    connection.sock = _FakeSocket(raw_response)
    return connection


@contextlib.contextmanager
def _rasp(use_body=False):
    from ddtrace.appsec._asm_request_context import _RASP_SUBCONTEXT

    env = mock.Mock(downstream_requests=0)
    calls = []

    def waf(addresses, **kwargs):
        calls.append((kwargs["rule_type"], addresses, core.find_item(_RASP_SUBCONTEXT)))

    with (
        mock.patch.object(httplib_appsec, "get_rasp_capability", return_value=True),
        mock.patch.object(httplib_appsec, "get_active_asm_context", return_value=env),
        mock.patch.object(httplib_appsec, "should_analyze_body_response", return_value=use_body),
        mock.patch.object(httplib_appsec, "call_waf_callback", side_effect=waf),
    ):
        yield calls


@pytest.mark.parametrize("use_body", [False, True])
def test_a_standalone_request_and_its_response_share_one_subcontext(use_body):
    """With no client above http.client, the request is the whole operation, response included."""
    with _rasp(use_body=use_body) as calls:
        connection = _connection()
        connection.request("POST", "/api", body='{"q": 1}', headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        # Analyzing the body must not consume it for the application.
        assert response.read() == b'{"a": true}'

    assert [rule for rule, _, _ in calls] == [EXPLOIT_PREVENTION.TYPE.SSRF_REQ, EXPLOIT_PREVENTION.TYPE.SSRF_RES]
    request_addresses, response_addresses = calls[0][1], calls[1][1]
    assert request_addresses[EXPLOIT_PREVENTION.ADDRESS.SSRF] == "http://downstream.test:8080/api"
    assert response_addresses["DOWN_RES_STATUS"] == "200"
    if use_body:
        assert request_addresses["DOWN_REQ_BODY"] == {"q": 1}
        assert response_addresses["DOWN_RES_BODY"] == {"a": True}
    else:
        assert "DOWN_REQ_BODY" not in request_addresses
        assert "DOWN_RES_BODY" not in response_addresses
    assert calls[0][2] is not None
    assert calls[1][2] is calls[0][2]
    assert not httplib_appsec._PENDING_RESPONSES


@pytest.mark.parametrize("status, analyzed", [(b"200 OK", False), (b"302 Found", True)], ids=["final", "redirect"])
def test_a_client_transport_above_keeps_the_final_response(status, analyzed):
    """urllib, urllib3 and requests analyze the final response themselves, so only redirects are reported here."""
    from ddtrace.appsec._asm_request_context import SSRF_CLIENT_TRANSPORT

    with _rasp() as calls:
        connection = _connection(b"HTTP/1.1 " + status + b"\r\nContent-Length: 0\r\n\r\n")
        with core.context_with_data("test.client_transport"):
            core.set_item(SSRF_CLIENT_TRANSPORT, True)
            # A redirect hop: the client consumed its original URL on the first one.
            connection.request("GET", "/next")
            connection.getresponse()

    expected = [EXPLOIT_PREVENTION.TYPE.SSRF_REQ] + ([EXPLOIT_PREVENTION.TYPE.SSRF_RES] if analyzed else [])
    assert [rule for rule, _, _ in calls] == expected
    assert calls[0][1][EXPLOIT_PREVENTION.ADDRESS.SSRF] == "http://downstream.test:8080/next"


def test_an_unread_response_does_not_leak_into_the_next_request():
    """A kept-alive connection's next request supersedes the analysis left pending by the previous one."""
    from ddtrace.appsec._asm_request_context import SSRF_CLIENT_TRANSPORT

    with _rasp() as calls:
        connection = _connection()
        connection.request("GET", "/first")
        assert connection in httplib_appsec._PENDING_RESPONSES
        connection._HTTPConnection__state = "Idle"
        connection._HTTPConnection__response = None
        with core.context_with_data("test.client_transport"):
            core.set_item(SSRF_CLIENT_TRANSPORT, True)
            connection.request("GET", "/second")
            connection.getresponse()

    assert [rule for rule, _, _ in calls] == [EXPLOIT_PREVENTION.TYPE.SSRF_REQ, EXPLOIT_PREVENTION.TYPE.SSRF_REQ]
    assert not httplib_appsec._PENDING_RESPONSES


@pytest.mark.parametrize("internal", ["no_trace", "agent"])
def test_ddtrace_own_connections_publish_nothing(internal):
    """The tracer's own http.client traffic is not the application's downstream traffic."""
    from urllib.parse import urlparse

    from ddtrace import tracer
    from ddtrace.internal.constants import _HTTPLIB_NO_TRACE_REQUEST

    agent = urlparse(tracer.agent_trace_url)
    with _rasp() as calls:
        connection = _connection()
        if internal == "no_trace":
            setattr(connection, _HTTPLIB_NO_TRACE_REQUEST, True)
        else:
            connection.host, connection.port = agent.hostname, agent.port
        with mock.patch.object(core, "context_with_event", wraps=core.context_with_event) as published:
            connection.request("GET", "/v0.4/traces")
            connection.getresponse()
        published.assert_not_called()

    assert calls == []
    assert not httplib_appsec._PENDING_RESPONSES


def test_the_event_never_exposes_http_client_shared_default_headers():
    """A subscriber writing to the event's headers must not change every later request's defaults."""
    import http.client

    seen = []

    def on_started(ctx):
        ctx.event.request_headers["X-Injected"] = "1"
        seen.append(ctx.event.request_headers)

    core.on("context.started." + HttpClientEvents.HTTPLIB_REQUEST.value, on_started, "test_default_headers")
    try:
        connection = _connection()
        connection.request("GET", "/")
    finally:
        core.reset_listeners("context.started." + HttpClientEvents.HTTPLIB_REQUEST.value, on_started)

    assert seen == [{"X-Injected": "1"}]
    assert http.client.HTTPConnection.request.__defaults__ == (None, {})


def test_appsec_owns_the_http_client_hooks():
    """The httplib integration installs only its spans; AppSec installs and removes the observation hooks."""
    from ddtrace.contrib.internal.httplib.patch import patch as httplib_patch
    from ddtrace.contrib.internal.httplib.patch import unpatch as httplib_unpatch

    httplib_unlisten()
    httplib_unpatch()
    assert ("http.client", "HTTPConnection.request") not in _WRAPPING_CONTEXTS
    try:
        httplib_patch()
        assert ("http.client", "HTTPConnection.request") not in _WRAPPING_CONTEXTS
        httplib_listen()
        assert isinstance(_WRAPPING_CONTEXTS.get(("http.client", "HTTPConnection.request")), HttpClientRequestContext)
        httplib_unlisten()
        assert ("http.client", "HTTPConnection.request") not in _WRAPPING_CONTEXTS
        assert ("http.client", "HTTPConnection.getresponse") not in _WRAPPING_CONTEXTS
    finally:
        httplib_unpatch()
