from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
import threading

from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ProviderNotReadyError
from openfeature.provider import ProviderStatus
import pytest

from ddtrace.internal.openfeature._config import _get_ffe_config
from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.openfeature import DataDogProvider
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config
from tests.utils import override_global_config


class FixtureState:
    def __init__(self):
        self.fixture = "valid_control"
        self.etag = '"ufc-v1"'
        self.requests_total = 0
        self.last_auth_present = False
        self.last_if_none_match = None
        self.last_status_code = None
        self.lock = threading.Lock()

    def valid_control_bytes(self):
        return json.dumps(create_config(create_boolean_flag("valid-control", enabled=True, default_value=True))).encode(
            "utf-8"
        )


class FixtureHandler(BaseHTTPRequestHandler):
    state = FixtureState()

    def do_GET(self):
        with self.state.lock:
            self.state.requests_total += 1
            self.state.last_auth_present = bool(self.headers.get("DD-API-KEY"))
            self.state.last_if_none_match = self.headers.get("If-None-Match")

        if not self.headers.get("DD-API-KEY"):
            self._send(401, b"")
            return

        if self.state.fixture in ("malformed_cold", "malformed_warm"):
            self._send(200, b'{"flags": ', {"ETag": '"bad-ufc"'})
            return

        if self.state.fixture == "unchanged_etag_304" and self.headers.get("If-None-Match") == self.state.etag:
            self._send(304, b"")
            return

        self._send(200, self.state.valid_control_bytes(), {"ETag": self.state.etag})

    def _send(self, status, body, headers=None):
        with self.state.lock:
            self.state.last_status_code = status
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


@pytest.fixture(autouse=True)
def clear_config():
    _set_ffe_config(None)
    yield
    _set_ffe_config(None)


@pytest.fixture
def fixture_server():
    state = FixtureState()

    class Handler(FixtureHandler):
        pass

    Handler.state = state
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, "http://127.0.0.1:%d/mock/ufc/config" % server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def _set_cdn_env(monkeypatch, url, api_key="test-api-key"):
    monkeypatch.setenv("DD_FLAGGING_SOURCE_MODE", "cdn")
    monkeypatch.setenv("DD_FLAGGING_CDN_BASE_URL", url)
    monkeypatch.setenv("DD_FLAGGING_CDN_POLL_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("DD_FLAGGING_CDN_REQUEST_TIMEOUT_SECONDS", "1")
    if api_key is None:
        monkeypatch.delenv("DD_API_KEY", raising=False)
    else:
        monkeypatch.setenv("DD_API_KEY", api_key)


def test_valid_control_cdn_initialize_ready_and_evaluates_from_native_path(monkeypatch, fixture_server):
    state, url = fixture_server
    _set_cdn_env(monkeypatch, url)
    with override_global_config({"experimental_flagging_provider_enabled": True}):
        provider = DataDogProvider(initialization_timeout=1.0)

        provider.initialize(EvaluationContext(targeting_key="user-123"))
        result = provider.resolve_boolean_details("valid-control", False)
        provider.shutdown()

    assert state.fixture == "valid_control"
    assert state.last_auth_present is True
    assert state.last_status_code == 200
    assert provider._status == ProviderStatus.NOT_READY
    assert result.value is True


def test_missing_auth_cold_raises_provider_not_ready(monkeypatch, fixture_server):
    _, url = fixture_server
    _set_cdn_env(monkeypatch, url, api_key=None)
    with override_global_config({"experimental_flagging_provider_enabled": True}):
        provider = DataDogProvider(initialization_timeout=1.0)

        with pytest.raises(ProviderNotReadyError):
            provider.initialize(EvaluationContext(targeting_key="user-123"))

    assert _get_ffe_config() is None


def test_malformed_cold_fails_and_malformed_warm_preserves_last_known_good(monkeypatch, fixture_server):
    state, url = fixture_server
    _set_cdn_env(monkeypatch, url)
    with override_global_config({"experimental_flagging_provider_enabled": True}):
        cold_provider = DataDogProvider(initialization_timeout=1.0)
        state.fixture = "malformed_cold"
        with pytest.raises(ProviderNotReadyError):
            cold_provider.initialize(EvaluationContext(targeting_key="user-123"))
        assert _get_ffe_config() is None

        state.fixture = "valid_control"
        warm_provider = DataDogProvider(initialization_timeout=1.0)
        warm_provider.initialize(EvaluationContext(targeting_key="user-123"))
        assert warm_provider.resolve_boolean_details("valid-control", False).value is True

        state.fixture = "malformed_warm"
        malformed_warm = warm_provider._cdn_source.poll_once()

        assert malformed_warm.error is not None
        assert warm_provider.resolve_boolean_details("valid-control", False).value is True
        warm_provider.shutdown()


def test_shutdown_cancels_delivery_worker(monkeypatch, fixture_server):
    _, url = fixture_server
    _set_cdn_env(monkeypatch, url)
    with override_global_config({"experimental_flagging_provider_enabled": True}):
        provider = DataDogProvider(initialization_timeout=1.0)
        provider.initialize(EvaluationContext(targeting_key="user-123"))
        source = provider._cdn_source

        assert source.is_running is True
        provider.shutdown()

    assert source.is_running is False
    assert provider._status == ProviderStatus.NOT_READY
    assert not provider._config_received.is_set()


def test_remote_config_mode_keeps_existing_rc_path(monkeypatch):
    monkeypatch.setenv("DD_FLAGGING_SOURCE_MODE", "remote_config")
    calls = []

    monkeypatch.setattr("ddtrace.internal.openfeature._provider.enable_featureflags_rc", lambda: calls.append("enable"))
    monkeypatch.setattr(
        "ddtrace.internal.openfeature._provider.disable_featureflags_rc", lambda: calls.append("disable")
    )
    with override_global_config({"experimental_flagging_provider_enabled": True}):
        provider = DataDogProvider(initialization_timeout=1.0)
        _set_ffe_config(object())

        provider.initialize(EvaluationContext(targeting_key="user-123"))
        provider.shutdown()

    assert calls == ["enable", "disable"]
    assert provider._cdn_source is None


def test_offline_mode_is_reserved_no_network(monkeypatch):
    monkeypatch.setenv("DD_FLAGGING_SOURCE_MODE", "offline")
    constructed_sources = []
    monkeypatch.setattr(
        "ddtrace.internal.openfeature._provider.PythonCdnSource", lambda *args, **kwargs: constructed_sources.append(1)
    )

    with override_global_config({"experimental_flagging_provider_enabled": True}):
        provider = DataDogProvider(initialization_timeout=0.01)
        with pytest.raises(ProviderNotReadyError):
            provider.initialize(EvaluationContext(targeting_key="user-123"))

    assert constructed_sources == []
    assert provider._status == ProviderStatus.NOT_READY
