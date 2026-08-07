"""
End-to-end wiring tests for agentless Feature Flagging delivery: the JSON:API
CDN response -> parse -> apply -> native config -> OpenFeature evaluation path,
and the provider lifecycle that starts/stops the agentless poller.
"""

import json

from openfeature.evaluation_context import EvaluationContext
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
from tests.utils import override_global_config


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
        provider = DataDogProvider()
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
