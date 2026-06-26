import json
import threading
from urllib.error import HTTPError

from ddtrace.internal.openfeature._cdn import PythonCdnSource
from ddtrace.internal.openfeature._source import FeatureFlagCdnConfig
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config


class FakeHeaders(dict):
    def get(self, name, default=None):
        return super().get(name, default)


class FakeResponse:
    def __init__(self, status, body=b"", headers=None):
        self.status = status
        self.headers = FakeHeaders(headers or {})
        self._body = body

    def read(self):
        return self._body

    def close(self):
        pass


class RecordingOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(
            {
                "url": request.full_url,
                "headers": dict(request.header_items()),
                "timeout": timeout,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, HTTPError):
            raise response
        return response


def _valid_control_bytes():
    return json.dumps(create_config(create_boolean_flag("valid-control", enabled=True, default_value=True))).encode(
        "utf-8"
    )


def _config(**kwargs):
    values = {
        "base_url": "http://127.0.0.1:8123/mock/ufc/config",
        "api_key": "test-api-key",
        "poll_interval_seconds": 60.0,
        "request_timeout_seconds": 1.0,
        "max_retries": 0,
        "backoff_base_seconds": 0.0,
    }
    values.update(kwargs)
    return FeatureFlagCdnConfig(**values)


def test_valid_control_sends_auth_applies_bytes_and_uses_etag(monkeypatch):
    applied_payloads = []

    def process_ffe_configuration(payload):
        applied_payloads.append(payload)
        return True

    monkeypatch.setattr("ddtrace.internal.openfeature._cdn.process_ffe_configuration", process_ffe_configuration)
    opener = RecordingOpener(
        [
            FakeResponse(200, _valid_control_bytes(), {"ETag": '"ufc-v1"'}),
            FakeResponse(304, b"", {}),
        ]
    )
    source = PythonCdnSource(_config(), urlopen=opener)

    valid_control = source.poll_once()
    unchanged_etag_304 = source.poll_once()

    assert valid_control.applied is True
    assert valid_control.status_code == 200
    assert applied_payloads == [_valid_control_bytes()]
    assert opener.requests[0]["headers"]["Dd-api-key"] == "test-api-key"

    assert unchanged_etag_304.unchanged is True
    assert unchanged_etag_304.status_code == 304
    assert opener.requests[1]["headers"]["If-none-match"] == '"ufc-v1"'
    assert applied_payloads == [_valid_control_bytes()]


def test_retries_only_429_and_5xx(monkeypatch):
    monkeypatch.setattr("ddtrace.internal.openfeature._cdn.process_ffe_configuration", lambda payload: True)
    retrying_opener = RecordingOpener(
        [
            HTTPError("http://127.0.0.1:8123/mock/ufc/config", 500, "server error", {}, None),
            HTTPError("http://127.0.0.1:8123/mock/ufc/config", 429, "rate limited", {}, None),
            FakeResponse(200, _valid_control_bytes(), {"ETag": '"ufc-v1"'}),
        ]
    )
    source = PythonCdnSource(_config(max_retries=2), urlopen=retrying_opener)

    result = source.poll_once()

    assert result.applied is True
    assert result.attempts == 3
    assert len(retrying_opener.requests) == 3

    non_retrying_opener = RecordingOpener(
        [HTTPError("http://127.0.0.1:8123/mock/ufc/config", 401, "unauthorized", {}, None)]
    )
    source = PythonCdnSource(_config(max_retries=2), urlopen=non_retrying_opener)

    missing_auth_cold = source.poll_once()

    assert missing_auth_cold.applied is False
    assert missing_auth_cold.error is not None
    assert missing_auth_cold.error.status_code == 401
    assert len(non_retrying_opener.requests) == 1


def test_delayed_no_overlap_skips_concurrent_poll(monkeypatch):
    monkeypatch.setattr("ddtrace.internal.openfeature._cdn.process_ffe_configuration", lambda payload: True)
    entered = threading.Event()
    release = threading.Event()
    active = 0
    max_active = 0
    active_lock = threading.Lock()

    def delayed_no_overlap(request, timeout):
        nonlocal active, max_active
        with active_lock:
            active += 1
            max_active = max(max_active, active)
        entered.set()
        assert release.wait(timeout=2.0)
        with active_lock:
            active -= 1
        return FakeResponse(200, _valid_control_bytes(), {"ETag": '"ufc-v1"'})

    source = PythonCdnSource(_config(), urlopen=delayed_no_overlap)
    results = []
    worker = threading.Thread(target=lambda: results.append(source.poll_once()))

    worker.start()
    assert entered.wait(timeout=2.0)
    skipped = source.poll_once()
    release.set()
    worker.join(timeout=2.0)

    assert skipped.skipped is True
    assert skipped.applied is False
    assert results[0].applied is True
    assert max_active == 1
