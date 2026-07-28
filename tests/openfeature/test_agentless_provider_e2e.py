"""
End-to-end wiring tests for agentless Feature Flagging delivery: the JSON:API
CDN response -> parse -> apply -> native config -> OpenFeature evaluation path,
and the provider lifecycle that starts/stops the agentless poller.
"""

import json

from openfeature.evaluation_context import EvaluationContext
import pytest

import ddtrace.internal.openfeature._agentless_source as source_mod
from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._native import process_ffe_configuration
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


def test_disabled_provider_starts_no_source():
    """The kill switch disables the provider and starts no agentless poller."""
    with override_global_config({"feature_flags_enabled": False, "_dd_api_key": "secret"}):
        provider = DataDogProvider()
        provider.initialize(EvaluationContext())

        assert provider._configuration_source is None
