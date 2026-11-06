"""
End-to-end wiring tests for agentless Feature Flagging delivery: the JSON:API
CDN response -> parse -> apply -> native config -> OpenFeature evaluation path,
and the provider lifecycle that starts/stops the agentless poller.
"""

import json
import logging
import time

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode
from openfeature.exception import ProviderNotReadyError
from openfeature.flag_evaluation import Reason
from openfeature.provider import ProviderStatus
import pytest

import ddtrace.internal.openfeature._agentless_source as source_mod
from ddtrace.internal.openfeature._config import _get_ffe_config
from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.internal.openfeature._provider import _apply_agentless_configuration
from ddtrace.internal.openfeature._source_selection import create_agentless_source
from ddtrace.internal.settings.openfeature import config as ffe_config
from ddtrace.openfeature import DataDogProvider
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config
from tests.openfeature.conftest import set_openfeature_provider
from tests.openfeature.conftest import use_configuration_source
from tests.utils import override_global_config


MISSING_API_KEY = "DD_API_KEY is required for the default Datadog Feature Flagging endpoint"
# A provider that waits for this timeout instead of failing at once fails the elapsed checks.
LONG_INITIALIZATION_TIMEOUT = 30.0


def _jsonapi_response(*flags):
    """Wrap a UFC config as the JSON:API envelope the agentless CDN returns."""
    return json.dumps(
        {"data": {"id": "1", "type": "universal-flag-configuration", "attributes": create_config(*flags)}}
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
    def __init__(self, response):
        self._response = response

    def request(self, *args, **kwargs):
        pass

    def getresponse(self):
        return self._response

    def close(self):
        pass


@pytest.fixture(autouse=True)
def clear_config():
    _set_ffe_config(None)
    yield
    _set_ffe_config(None)


@pytest.fixture
def mock_cdn(monkeypatch):
    def install(body):
        monkeypatch.setattr(
            source_mod,
            "get_connection",
            lambda url, timeout=None: _FakeConn(_FakeResponse(200, body, {"ETag": '"v1"'})),
        )

    return install


def test_agentless_delivery_evaluates_flag(mock_cdn):
    """One agentless poll delivers a flag that the provider then evaluates."""
    mock_cdn(_jsonapi_response(create_boolean_flag("my-flag", enabled=True, default_value=True)))

    with override_global_config({"_dd_api_key": "secret", "_dd_site": "datadoghq.com"}):
        provider = DataDogProvider()

        # The provider builds this same source in its lifecycle; drive one poll
        # synchronously so the assertion is deterministic (no background thread).
        source = create_agentless_source(ffe_config, process_ffe_configuration)
        assert source is not None
        source._retry_delay = lambda attempt: 0.0
        source.periodic()

        result = provider.resolve_boolean_details("my-flag", False)

    assert result.value is True
    assert result.variant == "true"


def test_provider_lifecycle_starts_and_stops_source(mock_cdn):
    """initialize() starts the agentless poller; shutdown() stops it."""
    mock_cdn(_jsonapi_response(create_boolean_flag("my-flag", enabled=True, default_value=True)))

    with override_global_config({"_dd_api_key": "secret", "_dd_site": "datadoghq.com"}):
        provider = DataDogProvider(initialization_timeout=5.0)
        try:
            provider.initialize(EvaluationContext())
            assert provider._configuration_source is not None

            # The poller runs on a background thread and polls immediately; wait
            # for the config to be applied.
            assert provider._config_received.wait(timeout=5.0)

            result = provider.resolve_boolean_details("my-flag", False)
            assert result.value is True
        finally:
            provider.shutdown()

        assert provider._configuration_source is None


# Attributes that pass JSON:API validation (``createdAt`` is a string) but that the
# native evaluator refuses because the timestamp is unparsable. Note the evaluator
# tolerates malformed individual flags, so an invalid timestamp is the realistic way
# a delivered payload gets rejected.
_REJECTED_ATTRIBUTES = {
    "format": "SERVER",
    "createdAt": "not-a-timestamp",
    "environment": {"name": "production"},
    "flags": {},
}


def test_evaluator_rejection_is_reported_as_failure():
    """A payload the native evaluator refuses must not report success.

    ``process_ffe_configuration`` returns False instead of raising, so the
    agentless apply wrapper turns that into an error; otherwise the source would
    advance its ETag past a configuration it never loaded.
    """
    assert process_ffe_configuration(_REJECTED_ATTRIBUTES) is False
    with pytest.raises(ValueError):
        _apply_agentless_configuration(_REJECTED_ATTRIBUTES)


def test_etag_not_advanced_when_evaluator_rejects_payload(monkeypatch):
    """Regression: a rejected payload must leave the ETag (and config) untouched.

    Otherwise the next poll sends If-None-Match, receives 304, and the stale
    configuration is kept indefinitely.
    """
    body = json.dumps(
        {"data": {"id": "1", "type": "universal-flag-configuration", "attributes": _REJECTED_ATTRIBUTES}}
    ).encode("utf-8")
    monkeypatch.setattr(
        source_mod,
        "get_connection",
        lambda url, timeout=None: _FakeConn(_FakeResponse(200, body, {"ETag": '"rejected"'})),
    )

    src = source_mod.AgentlessConfigurationSource(
        endpoint="https://ufc-server.ff-cdn.datadoghq.com/api/v2/feature-flagging/config/rules-based/server",
        apply_configuration=_apply_agentless_configuration,
        api_key="secret",
    )
    monkeypatch.setattr(src, "_retry_delay", lambda attempt: 0.0)

    src.periodic()

    assert src._etag is None  # not advanced
    assert _get_ffe_config() is None  # nothing applied


def test_disabled_provider_starts_no_source():
    """The kill switch disables the provider and starts no agentless poller."""
    with override_global_config({"feature_flags_enabled": False, "_dd_api_key": "secret"}):
        provider = DataDogProvider()
        provider.initialize(EvaluationContext())

        assert provider._configuration_source is None


def test_initialize_fails_at_once_without_api_key(caplog):
    """Without DD_API_KEY no configuration can arrive, so initialize() fails without waiting."""
    with override_global_config({"_dd_api_key": None}):
        provider = DataDogProvider(initialization_timeout=LONG_INITIALIZATION_TIMEOUT)
        try:
            with caplog.at_level(logging.ERROR, logger="ddtrace.internal.openfeature._provider"):
                start = time.monotonic()
                with pytest.raises(ProviderNotReadyError, match=f"^{MISSING_API_KEY}"):
                    provider.initialize(EvaluationContext())
                elapsed = time.monotonic() - start

            assert elapsed < 10.0, f"initialize() waited {elapsed:.2f}s"
            assert provider._configuration_source is None
            assert any(
                message.startswith(f"Feature Flags: agentless configuration source cannot start: {MISSING_API_KEY}")
                for message in caplog.messages
            ), caplog.messages
        finally:
            provider.shutdown()


def test_openfeature_client_reports_error_without_api_key():
    """Registration reports PROVIDER_ERROR at once and evaluations return the default value."""
    with override_global_config({"_dd_api_key": None}):
        provider = DataDogProvider(initialization_timeout=LONG_INITIALIZATION_TIMEOUT)
        try:
            start = time.monotonic()
            set_openfeature_provider(provider)
            elapsed = time.monotonic() - start

            client = api.get_client()
            details = client.get_boolean_details("my-flag", True)

            assert elapsed < 10.0, f"registration waited {elapsed:.2f}s"
            assert client.get_provider_status() == ProviderStatus.ERROR
            assert details.value is True
            assert details.reason == Reason.ERROR
            assert details.error_code == ErrorCode.PROVIDER_NOT_READY
        finally:
            api.clear_providers()


def test_initialize_retries_source_start_after_shutdown(mock_cdn):
    """Each initialization re-checks the source, so a later attempt with a key succeeds."""
    mock_cdn(_jsonapi_response(create_boolean_flag("my-flag", enabled=True, default_value=True)))
    provider = DataDogProvider(initialization_timeout=5.0)
    try:
        with override_global_config({"_dd_api_key": None}):
            with pytest.raises(ProviderNotReadyError, match=f"^{MISSING_API_KEY}"):
                provider.initialize(EvaluationContext())
        provider.shutdown()

        with override_global_config({"_dd_api_key": "secret", "_dd_site": "datadoghq.com"}):
            provider.initialize(EvaluationContext())

        assert provider._configuration_source is not None
        assert provider.resolve_boolean_details("my-flag", False).value is True
    finally:
        provider.shutdown()


def test_custom_endpoint_without_api_key_initializes(mock_cdn, monkeypatch):
    """A custom endpoint needs no API key, so initialize() waits for and applies its configuration."""
    mock_cdn(_jsonapi_response(create_boolean_flag("my-flag", enabled=True, default_value=True)))
    use_configuration_source(
        monkeypatch, DD_FEATURE_FLAGS_CONFIGURATION_SOURCE_AGENTLESS_BASE_URL="http://127.0.0.1:8130"
    )

    with override_global_config({"_dd_api_key": None}):
        provider = DataDogProvider(initialization_timeout=5.0)
        try:
            provider.initialize(EvaluationContext())

            assert provider._configuration_source is not None
            assert provider._configuration_source._api_key is None
            assert provider.resolve_boolean_details("my-flag", False).value is True
        finally:
            provider.shutdown()


def test_remote_config_source_does_not_require_api_key(monkeypatch):
    """Agent delivery needs no API key, so initialize() waits for configuration as before."""
    use_configuration_source(monkeypatch, DD_FEATURE_FLAGS_CONFIGURATION_SOURCE="remote_config")

    with override_global_config({"_dd_api_key": None}):
        provider = DataDogProvider(initialization_timeout=0.2)
        try:
            with pytest.raises(ProviderNotReadyError, match="before the initialization timeout"):
                provider.initialize(EvaluationContext())

            assert provider._configuration_source is None
            assert provider._configuration_source_error is None
        finally:
            provider.shutdown()
