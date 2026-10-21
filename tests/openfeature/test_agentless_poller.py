import gzip
import json
import logging
import socket

import pytest

from ddtrace.internal.constants import _HTTPLIB_NO_TRACE_REQUEST
from ddtrace.internal.native import ConnectionFailedError
import ddtrace.internal.openfeature._agentless_source as source_mod
from ddtrace.internal.openfeature._agentless_source import MAX_POLL_INTERVAL_SECONDS
from ddtrace.internal.openfeature._agentless_source import AgentlessConfigurationSource


ENDPOINT = "https://ufc-server.ff-cdn.datadoghq.com/api/v2/feature-flagging/config/rules-based/server?dd_env=prod"


def _ufc_body():
    return json.dumps(
        {
            "data": {
                "id": "1",
                "type": "universal-flag-configuration",
                "attributes": {
                    "format": "SERVER",
                    "createdAt": "2024-01-01T00:00:00Z",
                    "environment": {"name": "production"},
                    "flags": {"my-flag": {"enabled": True}},
                },
            }
        }
    ).encode("utf-8")


class _FakeResponse:
    def __init__(self, status, body=b"", headers=None):
        self.status = status
        self._body = body
        self._headers = {k.lower(): v for k, v in (headers or {}).items()}

    def read(self):
        return self._body

    def getheader(self, name, default=None):
        return self._headers.get(name.lower(), default)


class _FakeSocket:
    """Records the half-close a shutdown performs to cancel an in-flight poll."""

    def __init__(self, error=None):
        self.shutdowns: list = []
        self._error = error

    def shutdown(self, how):
        if self._error is not None:
            raise self._error
        self.shutdowns.append(how)


class _FakeConn:
    def __init__(self, responses, requests, sock=None):
        self._responses = responses
        self._requests = requests
        self.closed = False
        # http.client connects lazily, so a connection only grows a socket once
        # the request is on the wire. Tests that exercise cancellation set one.
        self.sock = sock

    def request(self, method, target, body, headers):
        self._requests.append(
            {
                "method": method,
                "target": target,
                "headers": headers,
                "no_trace": getattr(self, _HTTPLIB_NO_TRACE_REQUEST, False),
            }
        )

    def getresponse(self):
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed = True


@pytest.fixture
def harness(monkeypatch):
    """Return a factory building a poller with a scripted fake HTTP layer."""

    def build(responses, api_key=None, poll_interval=30.0, sock=None, env=None, endpoint=ENDPOINT):
        requests: list = []
        applied: list = []
        conns: list = []

        def fake_get_connection(url, timeout=None):
            conn = _FakeConn(responses, requests, sock=sock)
            conns.append(conn)
            return conn

        monkeypatch.setattr(source_mod, "get_connection", fake_get_connection)
        monkeypatch.setattr(source_mod, "_get_ffe_snapshot", lambda: object() if applied else None)

        src = AgentlessConfigurationSource(
            endpoint=endpoint,
            apply_configuration=applied.append,
            api_key=api_key,
            poll_interval=poll_interval,
            env=env,
        )
        # Keep retries instant.
        monkeypatch.setattr(src, "_retry_delay", lambda attempt: 0.0)
        src._requests = requests
        src._applied = applied
        src._conns = conns
        return src

    return build


def test_200_applies_and_advances_etag(harness):
    src = harness([_FakeResponse(200, _ufc_body(), {"ETag": '"v1"'})])
    src.periodic()
    assert len(src._applied) == 1
    assert src._applied[0]["environment"]["name"] == "production"
    assert src._etag == '"v1"'


def test_gzip_body_is_decoded(harness):
    body = gzip.compress(_ufc_body())
    src = harness([_FakeResponse(200, body, {"Content-Encoding": "gzip", "ETag": '"gz"'})])
    src.periodic()
    assert len(src._applied) == 1
    assert src._etag == '"gz"'


def test_304_is_noop_and_preserves_state(harness):
    src = harness([_FakeResponse(200, _ufc_body(), {"ETag": '"v1"'}), _FakeResponse(304)])
    src.periodic()
    src.periodic()
    assert len(src._applied) == 1  # only the first 200 applied
    assert src._etag == '"v1"'  # preserved across the 304


def test_blank_etag_clears_previous(harness):
    src = harness([_FakeResponse(200, _ufc_body(), {"ETag": '"v1"'}), _FakeResponse(200, _ufc_body(), {})])
    src.periodic()
    assert src._etag == '"v1"'
    src.periodic()
    assert src._etag is None


def test_401_warns_and_does_not_apply(harness):
    src = harness([_FakeResponse(401)])
    src.periodic()
    assert src._applied == []


def test_malformed_payload_preserves_last_known_good(harness):
    src = harness(
        [_FakeResponse(200, _ufc_body(), {"ETag": '"v1"'}), _FakeResponse(200, b"not json", {"ETag": '"v2"'})]
    )
    src.periodic()
    src.periodic()
    assert len(src._applied) == 1  # malformed second poll not applied
    assert src._etag == '"v1"'  # etag not advanced on malformed


def test_apply_failure_does_not_advance_etag(monkeypatch, harness):
    src = harness([_FakeResponse(200, _ufc_body(), {"ETag": '"v1"'})])

    def boom(_):
        raise RuntimeError("apply failed")

    monkeypatch.setattr(src, "_apply_configuration", boom)
    src.periodic()
    assert src._etag is None


def test_retryable_500_then_success(harness):
    src = harness([_FakeResponse(500), _FakeResponse(200, _ufc_body(), {"ETag": '"ok"'})])
    src.periodic()
    assert len(src._applied) == 1
    assert len(src._requests) == 2  # retried once


def test_network_error_is_retryable(harness):
    src = harness([OSError("boom"), _FakeResponse(200, _ufc_body(), {"ETag": '"ok"'})])
    src.periodic()
    assert len(src._applied) == 1
    assert len(src._requests) == 2


def test_retries_exhausted_no_apply(harness):
    src = harness([_FakeResponse(500), _FakeResponse(503), _FakeResponse(500)])
    src.periodic()
    assert src._applied == []
    assert len(src._requests) == 3  # MAX_ATTEMPTS


def test_non_retryable_status_not_retried(harness):
    src = harness([_FakeResponse(404)])
    src.periodic()
    assert src._applied == []
    assert len(src._requests) == 1  # 404 is not retried


def test_if_none_match_sent_when_etag_held(harness):
    src = harness([_FakeResponse(200, _ufc_body(), {"ETag": '"v1"'}), _FakeResponse(304)])
    src.periodic()
    src.periodic()
    assert "If-None-Match" not in src._requests[0]["headers"]
    assert src._requests[1]["headers"]["If-None-Match"] == '"v1"'


def test_api_key_header_present_and_absent(harness):
    with_key = harness([_FakeResponse(304)], api_key="secret")
    with_key.periodic()
    assert with_key._requests[0]["headers"]["DD-API-KEY"] == "secret"

    without_key = harness([_FakeResponse(304)], api_key=None)
    without_key.periodic()
    assert "DD-API-KEY" not in without_key._requests[0]["headers"]


def test_client_library_headers_and_gzip_accept(harness):
    src = harness([_FakeResponse(304)])
    src.periodic()
    headers = src._requests[0]["headers"]
    assert headers["Accept-Encoding"] == "gzip"
    assert headers["DD-Client-Library-Language"] == "python"
    assert headers["DD-Client-Library-Version"]


def test_self_tracing_suppressed(harness):
    src = harness([_FakeResponse(304)])
    src.periodic()
    assert src._requests[0]["no_trace"] is True


def _debug_messages(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]


def test_debug_logs_each_attempt_and_its_outcome(harness, caplog):
    src = harness([_FakeResponse(503), TimeoutError(), _FakeResponse(200, _ufc_body(), {"ETag": '"v1"'})], env="prod")
    with caplog.at_level(logging.DEBUG, logger=source_mod.__name__):
        src.periodic()

    messages = _debug_messages(caplog)
    assert messages == [
        "Feature Flags: agentless configuration poll attempt 1/3",
        "Feature Flags: agentless configuration poll attempt 1/3 returned HTTP 503; retrying in 0.0s",
        "Feature Flags: agentless configuration poll attempt 2/3",
        "Feature Flags: agentless configuration poll attempt 2/3 failed: TimeoutError; retrying in 0.0s",
        "Feature Flags: agentless configuration poll attempt 3/3",
        "Feature Flags: agentless configuration poll attempt 3/3 returned HTTP 200",
        "Feature Flags: agentless configuration applied successfully (1 flag(s), sdk_env=prod, requested_env=prod, "
        'configuration_env=production, created_at=2024-01-01T00:00:00Z, etag="v1")',
    ], messages


@pytest.mark.parametrize("recovers", [True, False], ids=["successful-retry", "exhausted-retries"])
@pytest.mark.parametrize("scheme", ["https:", "http:", ""])
def test_poll_failure_logs_redact_transport_urls(harness, caplog, recovers, scheme):
    endpoint = f"{scheme}//user:secret-password@flags.example.test/secret-path?token=secret-query"
    error_message = f"error sending request for url ({endpoint})"
    responses = [ConnectionFailedError(error_message)]
    if recovers:
        responses.append(_FakeResponse(200, _ufc_body()))
    else:
        responses.extend([ConnectionFailedError(error_message), ConnectionFailedError(error_message)])
    src = harness(responses)
    with caplog.at_level(logging.DEBUG, logger=source_mod.__name__):
        src.periodic()

    diagnostic = "ConnectionFailedError: error sending request for url (<endpoint>)"
    assert (f"Feature Flags: agentless configuration poll attempt 1/3 failed: {diagnostic}; retrying in 0.0s") in (
        _debug_messages(caplog)
    )
    for sensitive in ("secret-password", "secret-path", "secret-query", "flags.example.test"):
        assert sensitive not in caplog.text
    if recovers:
        assert len(src._applied) == 1
        assert "Feature Flags: agentless configuration poll attempt 2/3 returned HTTP 200" in caplog.messages
    else:
        assert src._applied == []
        assert f"Feature Flagging agentless request failed after 3 attempts: {diagnostic}" in caplog.messages


@pytest.mark.parametrize(
    "message,expected",
    [
        ("connection refused", "connection refused"),
        ("", ""),
        (
            "failed for 'https://user:secret@host/path?token=secret'",
            "failed for '<endpoint>'",
        ),
        (
            "failed for https://host/path'secret?token=secret",
            "failed for <endpoint>",
        ),
        (
            "redirect from https://host/secret to https://other/secret failed",
            "redirect from <endpoint> to <endpoint> failed",
        ),
    ],
)
def test_describe_error_redacts_urls(message, expected):
    error = ConnectionFailedError(message)
    assert source_mod._describe_error(error) == (
        f"ConnectionFailedError: {expected}" if expected else "ConnectionFailedError"
    )


def test_describe_error_handles_broken_str():
    class BrokenError(Exception):
        def __str__(self):
            raise ValueError("https://host/secret")

    assert source_mod._describe_error(BrokenError()) == "BrokenError"


def test_debug_logs_unchanged_304(harness, caplog):
    src = harness([_FakeResponse(304)])
    with caplog.at_level(logging.DEBUG, logger=source_mod.__name__):
        src.periodic()

    assert "Feature Flags: agentless configuration poll attempt 1/3 returned HTTP 304 (unchanged)" in (
        _debug_messages(caplog)
    )


def test_debug_logs_unset_env_and_attempt_count_restarts_per_poll(harness, caplog):
    src = harness([_FakeResponse(200, _ufc_body()), _FakeResponse(304)])
    with caplog.at_level(logging.DEBUG, logger=source_mod.__name__):
        src.periodic()
        src.periodic()

    messages = _debug_messages(caplog)
    assert (
        "Feature Flags: agentless configuration applied successfully (1 flag(s), sdk_env=unset, requested_env=prod, "
        "configuration_env=production, created_at=2024-01-01T00:00:00Z, etag=None)"
    ) in messages
    assert messages.count("Feature Flags: agentless configuration poll attempt 1/3") == 2, messages


def test_debug_logs_terminal_status_and_warns(harness, caplog):
    src = harness([_FakeResponse(404)])
    with caplog.at_level(logging.DEBUG, logger=source_mod.__name__):
        src.periodic()

    messages = _debug_messages(caplog)
    assert "Feature Flags: agentless configuration poll attempt 1/3 returned HTTP 404; not retrying" in messages
    assert (
        "Feature Flags: agentless configuration poll failed; no configuration is active, so evaluations return "
        "default values"
    ) in messages
    assert any(
        m.startswith("Feature Flagging agentless endpoint returned HTTP 404. Possible causes: ")
        for m in caplog.messages
    ), caplog.messages


def test_debug_logs_retained_configuration_and_recovery(harness, caplog):
    src = harness(
        [
            _FakeResponse(200, _ufc_body(), {"ETag": '"v1"'}),
            _FakeResponse(500),
            _FakeResponse(500),
            _FakeResponse(500),
            _FakeResponse(304),
        ]
    )
    with caplog.at_level(logging.DEBUG, logger=source_mod.__name__):
        src.periodic()
        src.periodic()
        src.periodic()

    messages = _debug_messages(caplog)
    assert "Feature Flags: agentless configuration poll attempt 3/3 returned HTTP 500; not retrying" in messages
    assert (
        "Feature Flags: agentless configuration poll failed; the previously applied configuration remains active"
    ) in messages
    assert "Feature Flags: agentless configuration poll attempt 1/3 returned HTTP 304 (unchanged)" in messages
    assert messages[-1] == 'Feature Flags: agentless configuration delivery recovered (etag="v1")'


def test_debug_logs_malformed_payload_reason(harness, caplog):
    src = harness([_FakeResponse(200, b"not json")])
    with caplog.at_level(logging.DEBUG, logger=source_mod.__name__):
        src.periodic()

    assert "Feature Flags: agentless configuration payload rejected: ValueError: Malformed UFC payload" in (
        _debug_messages(caplog)
    )


@pytest.mark.parametrize(
    "endpoint,api_key,requested_env,api_key_header",
    [
        (ENDPOINT, "secret", "prod", "sent"),
        ("https://flags.example.test/custom/path", None, "not sent", "not sent"),
    ],
    ids=["managed", "custom"],
)
def test_debug_logs_effective_settings_on_start(harness, caplog, endpoint, api_key, requested_env, api_key_header):
    src = harness([_FakeResponse(304)], api_key=api_key, env="prod", endpoint=endpoint)
    with caplog.at_level(logging.DEBUG, logger=source_mod.__name__):
        src.start()
        src.stop()
        src.join()

    assert (
        f"Feature Flags: agentless configuration source settings: sdk_env=prod, requested_env={requested_env}, "
        f"DD-API-KEY header {api_key_header}, poll interval 30.0s, request timeout 5.0s"
    ) in _debug_messages(caplog)
    assert "secret" not in caplog.text


def test_debug_does_not_report_rejected_payload_as_applied(harness, caplog, monkeypatch):
    src = harness([_FakeResponse(200, _ufc_body())])

    def reject(_):
        raise ValueError("rejected")

    monkeypatch.setattr(src, "_apply_configuration", reject)
    with caplog.at_level(logging.DEBUG, logger=source_mod.__name__):
        src.periodic()

    assert not any("applied successfully" in m for m in _debug_messages(caplog))


def _spy_waits(src, monkeypatch):
    """Record the positive delays the source waits on (ignores no-op 0 waits)."""
    waits: list = []

    def fake_wait(delay):
        if delay > 0:
            waits.append(delay)
        return False

    monkeypatch.setattr(src, "_wait", fake_wait)
    return waits


def test_origin_process_first_poll_is_not_jittered(harness, monkeypatch):
    src = harness([_FakeResponse(304)])
    waits = _spy_waits(src, monkeypatch)
    src.periodic()
    assert waits == []  # origin process polls immediately


def test_forked_child_first_poll_is_jittered_once(harness, monkeypatch):
    src = harness([_FakeResponse(304), _FakeResponse(304)])
    waits = _spy_waits(src, monkeypatch)
    # Simulate a forked worker: a PID different from the creating process.
    monkeypatch.setattr(source_mod.os, "getpid", lambda: src._origin_pid + 1)

    src.periodic()
    assert len(waits) == 1
    assert 0 < waits[0] <= min(src.interval, source_mod.FIRST_POLL_JITTER_MAX_S)

    src.periodic()
    assert len(waits) == 1  # only the first poll in the child is staggered


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


def test_shutdown_during_backoff_stops_retrying(harness, monkeypatch):
    """A shutdown requested while backing off must not start another attempt."""
    src = harness([_FakeResponse(500), _FakeResponse(200, _ufc_body(), {"ETag": '"late"'})])
    # A real backoff, so the fake wait below can tell it apart from the zero-length
    # initial_wait retry() performs before the first attempt. Nothing actually
    # sleeps: _wait is replaced outright.
    monkeypatch.setattr(src, "_retry_delay", lambda attempt: 30.0)

    def wait_then_shutdown(delay):
        # The real _wait returns immediately for a zero delay without observing a
        # stop, so only a genuine backoff wait may request one here.
        if not delay:
            return src._stopping
        src._stopping = True
        return True

    monkeypatch.setattr(src, "_wait", wait_then_shutdown)

    src.periodic()

    assert len(src._requests) == 1  # retry abandoned
    assert src._applied == []
    assert src._etag is None


def test_shutdown_mid_poll_does_not_apply(harness):
    """A response that arrives after shutdown must not replace state."""
    src = harness([_FakeResponse(200, _ufc_body(), {"ETag": '"v1"'})])
    src._stopping = True

    src.periodic()

    assert src._applied == []
    assert src._etag is None


def test_shutdown_half_closes_the_socket_of_a_poll_in_flight(harness):
    """A stop must tear down the open request instead of waiting out its timeout."""
    sock = _FakeSocket()
    src = harness([_FakeResponse(200, _ufc_body(), {"ETag": '"v1"'})], sock=sock)
    cancelled_at: list = []

    # Stand in for the thread calling stop() while the worker blocks on the read.
    original_getresponse = _FakeConn.getresponse

    def getresponse_with_concurrent_stop(conn):
        src._stopping = True
        src._cancel_in_flight_request()
        cancelled_at.append(list(sock.shutdowns))
        return original_getresponse(conn)

    _FakeConn.getresponse = getresponse_with_concurrent_stop
    try:
        src.periodic()
    finally:
        _FakeConn.getresponse = original_getresponse

    # Half-closed while the request was still open, not after it returned.
    assert cancelled_at == [[socket.SHUT_RDWR]]
    assert src._applied == []  # a cancelled poll keeps last-known-good
    assert src._etag is None


def test_shutdown_releases_the_connection_it_cancelled(harness):
    """The worker still owns cleanup: the cancelled connection is closed."""
    src = harness([_FakeResponse(200, _ufc_body(), {"ETag": '"v1"'})], sock=_FakeSocket())

    src.periodic()

    assert src._conns[0].closed is True
    assert src._in_flight_conn is None  # slot released for the next poll


def test_stop_service_cancels_before_joining(harness, monkeypatch):
    """_stop_service half-closes the live socket, then defers to PeriodicService."""
    sock = _FakeSocket()
    src = harness([_FakeResponse(304)], sock=sock)
    joined: list = []
    # PeriodicService._stop_service joins the worker; stub it out so the test needs
    # no running thread and can assert the cancel happened before the join.
    monkeypatch.setattr(
        source_mod.PeriodicService,
        "_stop_service",
        lambda self, *a, **kw: joined.append(list(sock.shutdowns)),
    )
    with src._conn_lock:
        src._in_flight_conn = _FakeConn([], [], sock=sock)

    src._stop_service()

    assert src._stopping is True
    assert joined == [[socket.SHUT_RDWR]]


def test_cancel_is_a_noop_when_no_poll_is_in_flight(harness):
    """Stopping an idle poller must not raise."""
    src = harness([_FakeResponse(304)])
    src._cancel_in_flight_request()  # no connection published yet
    assert src._in_flight_conn is None


def test_cancel_is_a_noop_before_the_socket_exists(harness):
    """http.client connects lazily; a stop with no socket yet has nothing to close."""
    src = harness([_FakeResponse(304)])
    with src._conn_lock:
        src._in_flight_conn = _FakeConn([], [], sock=None)

    src._cancel_in_flight_request()  # must not raise on the missing socket


def test_cancel_tolerates_an_already_closed_socket(harness):
    """A socket the worker closed first raises OSError; the stop swallows it."""
    src = harness([_FakeResponse(304)])
    with src._conn_lock:
        src._in_flight_conn = _FakeConn([], [], sock=_FakeSocket(error=OSError("not connected")))

    src._cancel_in_flight_request()  # must not propagate


def test_no_request_is_issued_after_a_stop_was_requested(harness):
    """A poll that starts after the stop flag is set never reaches the network."""
    src = harness([_FakeResponse(200, _ufc_body(), {"ETag": '"v1"'})])
    src._stopping = True

    src.periodic()

    assert src._requests == []  # returned before conn.request()
    assert src._applied == []


def test_backoff_wait_is_interruptible(harness):
    """The backoff wait returns as soon as a shutdown is requested."""
    src = harness([_FakeResponse(304)])
    src._stopping = True
    # A long delay must return immediately (True) rather than sleeping it out.
    assert src._wait(3600) is True


def test_wait_stops_at_the_next_slice(harness, monkeypatch):
    """A stop requested mid-wait ends it at the next slice, not after the full delay."""
    src = harness([_FakeResponse(304)])
    slept: list = []

    def fake_sleep(delay):
        slept.append(delay)
        src._stopping = True  # requested while the wait is in progress

    monkeypatch.setattr(source_mod.time, "sleep", fake_sleep)

    assert src._wait(3600) is True
    assert slept == [source_mod.SHUTDOWN_POLL_INTERVAL_S]  # one slice, not 3600s


def test_wait_sleeps_the_full_delay_when_not_stopping(harness, monkeypatch):
    """Without a stop request the wait consumes the whole delay in slices."""
    src = harness([_FakeResponse(304)])
    slept: list = []
    monkeypatch.setattr(source_mod.time, "sleep", slept.append)

    assert src._wait(source_mod.SHUTDOWN_POLL_INTERVAL_S * 3) is False
    assert len(slept) == 3


def test_poll_interval_clamped_to_one_hour():
    src = AgentlessConfigurationSource(
        endpoint=ENDPOINT,
        apply_configuration=lambda _: None,
        poll_interval=MAX_POLL_INTERVAL_SECONDS * 5,
    )
    assert src.interval == MAX_POLL_INTERVAL_SECONDS


@pytest.mark.parametrize("bad_interval", [0, -1, -30.0])
def test_non_positive_poll_interval_falls_back_to_default(bad_interval):
    """A non-positive interval would busy-loop against the CDN; use the default."""
    src = AgentlessConfigurationSource(
        endpoint=ENDPOINT,
        apply_configuration=lambda _: None,
        poll_interval=bad_interval,
    )
    assert src.interval == source_mod.DEFAULT_POLL_INTERVAL_SECONDS


@pytest.mark.parametrize("bad_timeout", [0, -1, -5.0])
def test_non_positive_request_timeout_falls_back_to_default(bad_timeout):
    src = AgentlessConfigurationSource(
        endpoint=ENDPOINT,
        apply_configuration=lambda _: None,
        request_timeout=bad_timeout,
    )
    assert src._request_timeout == source_mod.DEFAULT_REQUEST_TIMEOUT_SECONDS
