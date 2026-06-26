from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
import threading

from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode
from openfeature.exception import ProviderNotReadyError
from openfeature.provider import ProviderStatus
import pytest

from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._source import FeatureFlagCdnConfig
from ddtrace.openfeature import DataDogProvider
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config
from tests.utils import override_global_config


class FixtureState:
    def __init__(self):
        self.fixture = "valid_control"
        self.etag = '"hybrid-ufc-v1"'
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
        if self.state.fixture == "timeout":
            self._send(500, b"")
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


def _hybrid_config(url, api_key="test-api-key"):
    return FeatureFlagCdnConfig(
        base_url=url,
        api_key=api_key,
        poll_interval_seconds=60.0,
        request_timeout_seconds=1.0,
        max_retries=0,
        backoff_base_seconds=0.0,
        delivery="hybrid",
    )


def _set_hybrid_cdn_env(monkeypatch, url, api_key="test-api-key"):
    monkeypatch.setenv("DD_FLAGGING_SOURCE_MODE", "cdn")
    monkeypatch.setenv("DD_FLAGGING_CDN_DELIVERY", "hybrid")
    monkeypatch.setenv("DD_FLAGGING_CDN_BASE_URL", url)
    monkeypatch.setenv("DD_FLAGGING_CDN_POLL_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("DD_FLAGGING_CDN_REQUEST_TIMEOUT_SECONDS", "1")
    if api_key is None:
        monkeypatch.delenv("DD_API_KEY", raising=False)
    else:
        monkeypatch.setenv("DD_API_KEY", api_key)


def test_python_glue_passes_explicit_config_to_native_state_without_scheduler_fields(monkeypatch):
    from ddtrace.internal.openfeature._hybrid_source import HybridCdnSource

    captured = {}

    class FakeHybridSourceState:
        def __init__(
            self,
            base_url,
            api_key,
            request_timeout_seconds,
            max_retries,
            backoff_base_seconds,
        ):
            captured["base_url"] = base_url
            captured["api_key"] = api_key
            captured["request_timeout_seconds"] = request_timeout_seconds
            captured["max_retries"] = max_retries
            captured["backoff_base_seconds"] = backoff_base_seconds

    monkeypatch.setenv("DD_API_KEY", "hidden-env-key")

    HybridCdnSource(
        _hybrid_config("http://127.0.0.1:8123/mock/ufc/config", api_key="explicit-key"),
        source_state_cls=FakeHybridSourceState,
    )

    assert captured == {
        "base_url": "http://127.0.0.1:8123/mock/ufc/config",
        "api_key": "explicit-key",
        "request_timeout_seconds": 1.0,
        "max_retries": 0,
        "backoff_base_seconds": 0.0,
    }


def test_hybrid_python_scheduler_owns_no_overlap_and_shutdown():
    from ddtrace.internal.openfeature._hybrid_source import HybridCdnSource

    entered = threading.Event()
    release = threading.Event()

    class FakeStatus:
        applied = True
        unchanged = False
        skipped = False
        attempts = 1
        status_code = 200
        error = None
        retryable = False
        etag = '"hybrid"'

    class DelayedHybridSourceState:
        is_ready = True

        def __init__(self, *args):
            pass

        def poll_once(self):
            entered.set()
            assert release.wait(timeout=2.0)
            return FakeStatus()

    source = HybridCdnSource(
        _hybrid_config("http://127.0.0.1:8123/mock/ufc/config"),
        source_state_cls=DelayedHybridSourceState,
    )
    results = []
    worker = threading.Thread(target=lambda: results.append(source.poll_once()))
    worker.start()
    assert entered.wait(timeout=2.0)

    skipped = source.poll_once()
    release.set()
    worker.join(timeout=2.0)
    source.start()
    assert source.is_running is True
    assert source.shutdown(timeout=1.0) is True

    assert skipped.skipped is True
    assert results[0].applied is True
    assert source.is_running is False


def test_hybrid_provider_ready_evaluates_and_shutdown_is_visible(monkeypatch, fixture_server):
    from ddtrace.internal.openfeature._hybrid_source import HybridCdnSource

    state, url = fixture_server
    _set_hybrid_cdn_env(monkeypatch, url)

    with override_global_config({"experimental_flagging_provider_enabled": True}):
        provider = DataDogProvider(initialization_timeout=1.0)

        provider.initialize(EvaluationContext(targeting_key="user-123"))
        result = provider.resolve_boolean_details("valid-control", False)
        source = provider._cdn_source
        provider.shutdown()

    assert isinstance(source, HybridCdnSource)
    assert state.last_auth_present is True
    assert state.last_status_code == 200
    assert result.value is True
    assert provider._status == ProviderStatus.NOT_READY
    assert source.is_running is False


def test_hybrid_maps_cold_failures_to_not_ready(monkeypatch, fixture_server):
    _, url = fixture_server
    _set_hybrid_cdn_env(monkeypatch, url, api_key=None)

    with override_global_config({"experimental_flagging_provider_enabled": True}):
        provider = DataDogProvider(initialization_timeout=1.0)
        with pytest.raises(ProviderNotReadyError):
            provider.initialize(EvaluationContext(targeting_key="user-123"))
        result = provider.resolve_boolean_details("valid-control", False)

    assert result.value is False
    assert result.error_code == ErrorCode.PROVIDER_NOT_READY


def test_hybrid_preserves_lkg_for_malformed_warm_and_uses_etag(monkeypatch, fixture_server):
    state, url = fixture_server
    _set_hybrid_cdn_env(monkeypatch, url)

    with override_global_config({"experimental_flagging_provider_enabled": True}):
        provider = DataDogProvider(initialization_timeout=1.0)
        provider.initialize(EvaluationContext(targeting_key="user-123"))
        assert provider.resolve_boolean_details("valid-control", False).value is True

        state.fixture = "unchanged_etag_304"
        unchanged = provider._cdn_source.poll_once()
        assert unchanged.unchanged is True
        assert state.last_if_none_match == state.etag

        state.fixture = "malformed_warm"
        malformed = provider._cdn_source.poll_once()
        assert malformed.error is not None
        assert provider.resolve_boolean_details("valid-control", False).value is True
        provider.shutdown()


def test_hybrid_timeout_or_retry_exhaustion_is_not_ready(monkeypatch, fixture_server):
    state, url = fixture_server
    state.fixture = "timeout"
    _set_hybrid_cdn_env(monkeypatch, url)

    with override_global_config({"experimental_flagging_provider_enabled": True}):
        provider = DataDogProvider(initialization_timeout=1.0)
        with pytest.raises(ProviderNotReadyError):
            provider.initialize(EvaluationContext(targeting_key="user-123"))
