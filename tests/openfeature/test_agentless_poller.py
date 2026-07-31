import gzip
import json

import pytest

from ddtrace.internal.constants import _HTTPLIB_NO_TRACE_REQUEST
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


class _FakeConn:
    def __init__(self, responses, requests):
        self._responses = responses
        self._requests = requests

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
        pass


@pytest.fixture
def harness(monkeypatch):
    """Return a factory building a poller with a scripted fake HTTP layer."""

    def build(responses, api_key=None, poll_interval=30.0):
        requests: list = []
        applied: list = []

        def fake_get_connection(url, timeout=None):
            return _FakeConn(responses, requests)

        monkeypatch.setattr(source_mod, "get_connection", fake_get_connection)

        src = AgentlessConfigurationSource(
            endpoint=ENDPOINT,
            apply_configuration=applied.append,
            api_key=api_key,
            poll_interval=poll_interval,
        )
        # Keep retries instant.
        monkeypatch.setattr(src, "_retry_delay", lambda attempt: 0.0)
        src._requests = requests
        src._applied = applied
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

    def wait_then_shutdown(delay):
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
