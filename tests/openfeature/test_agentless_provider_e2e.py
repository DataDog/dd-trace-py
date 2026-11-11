"""
End-to-end wiring tests for agentless Feature Flagging delivery: the JSON:API
CDN response -> parse -> apply -> native config -> OpenFeature evaluation path,
and the provider lifecycle that starts/stops the agentless poller.
"""

from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
import json
import threading

from openfeature.evaluation_context import EvaluationContext
import pytest

import ddtrace
import ddtrace.internal.openfeature._agentless_source as source_mod
from ddtrace.internal.openfeature._config import _get_ffe_config
from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.internal.openfeature._provider import _apply_agentless_configuration
from ddtrace.internal.openfeature._source_selection import create_agentless_source
from ddtrace.internal.settings.openfeature import OpenFeatureConfig
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
    def __init__(self, response, requests=None):
        self._response = response
        self._requests = [] if requests is None else requests

    def request(self, method, target, body=None, headers=None):
        self._requests.append({"target": target, "headers": headers})

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
    """Serve body for each poll. Return the list that records the requests."""

    def install(body):
        requests: list = []
        monkeypatch.setattr(
            source_mod,
            "get_connection",
            lambda url, timeout=None: _FakeConn(_FakeResponse(200, body, {"ETag": '"v1"'}), requests),
        )
        return requests

    return install


@pytest.fixture
def local_cdn():
    """Serve the flag configuration from a local HTTP server. Yield its URL and the headers of each request.

    The requests use the real HTTP client of the tracer. That client refuses some header values.
    Thus these tests see the headers that really get to the endpoint.
    """
    received: list = []
    body = _jsonapi_response(create_boolean_flag("my-flag", enabled=True, default_value=True))

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.headers)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/flags", received
    finally:
        server.shutdown()
        server.server_close()


def _poll_local_cdn(url, service):
    """Set the service in code, as a user does. Then run one agentless poll against url."""
    custom = OpenFeatureConfig(source={"DD_FEATURE_FLAGS_CONFIGURATION_SOURCE_AGENTLESS_BASE_URL": url})
    with override_global_config({"service": service}):
        source = create_agentless_source(custom, process_ffe_configuration)
        assert source is not None
        source.periodic()
        # Only the header value changes. The tracer service does not change.
        assert ddtrace.config.service == service


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


def test_agentless_request_sends_tracer_service(mock_cdn):
    requests = mock_cdn(_jsonapi_response(create_boolean_flag("my-flag", enabled=True, default_value=True)))

    with override_global_config({"_dd_api_key": "secret", "_dd_site": "datadoghq.com", "service": "checkout"}):
        source = create_agentless_source(ffe_config, process_ffe_configuration)
        assert source is not None
        source.periodic()

    (request,) = requests
    assert request["target"].partition("?")[0] == "/api/v2/feature-flagging/config/rules-based/server"
    assert request["headers"]["DD-Service"] == "checkout"
    assert request["headers"]["DD-API-KEY"] == "secret"


def test_custom_endpoint_receives_service_but_not_api_key(mock_cdn):
    """A service name is not a secret, unlike the API key. Thus a custom endpoint also gets it."""
    requests = mock_cdn(_jsonapi_response(create_boolean_flag("my-flag", enabled=True, default_value=True)))
    custom = OpenFeatureConfig(
        source={"DD_FEATURE_FLAGS_CONFIGURATION_SOURCE_AGENTLESS_BASE_URL": "https://flags.example.test/custom"}
    )

    with override_global_config({"_dd_api_key": "secret", "service": "checkout"}):
        source = create_agentless_source(custom, process_ffe_configuration)
        assert source is not None
        source.periodic()

    (request,) = requests
    assert request["headers"]["DD-Service"] == "checkout"
    assert "DD-API-KEY" not in request["headers"]


@pytest.mark.subprocess(env={"DD_API_KEY": "secret", "DD_SERVICE": "checkout"})
def test_agentless_request_sends_dd_service_from_environment():
    import ddtrace.internal.openfeature._agentless_source as source_mod
    from ddtrace.internal.openfeature._source_selection import create_agentless_source
    from ddtrace.internal.settings.openfeature import config as ffe_config
    from tests.openfeature.test_agentless_provider_e2e import _FakeConn
    from tests.openfeature.test_agentless_provider_e2e import _FakeResponse

    requests = []
    source_mod.get_connection = lambda url, timeout=None: _FakeConn(_FakeResponse(304), requests)

    source = create_agentless_source(ffe_config, lambda _: None)
    assert source is not None
    source.periodic()

    (request,) = requests
    assert request["target"].partition("?")[0] == "/api/v2/feature-flagging/config/rules-based/server"
    assert request["headers"]["DD-Service"] == "checkout"


@pytest.mark.parametrize("service, expected", [("My Service", "my_service"), ("bad\nvalue", "bad_value")], ids=repr)
def test_request_carries_the_service_name_apm_shows(local_cdn, service, expected):
    url, received = local_cdn

    _poll_local_cdn(url, service)

    (headers,) = received
    assert headers["DD-Service"] == expected
    assert _get_ffe_config() is not None  # The configuration is applied.


# APM shows these as "_svc", "café-api", "支付" and no name. The CDN accepts none of them.
@pytest.mark.parametrize("service", ["_svc", "café-api", "支付", "___"], ids=repr)
def test_request_sent_without_service_when_the_cdn_rejects_the_apm_name(local_cdn, service):
    url, received = local_cdn

    _poll_local_cdn(url, service)

    (headers,) = received  # The poll still occurs.
    assert "DD-Service" not in headers
    assert _get_ffe_config() is not None  # The configuration is applied.


@pytest.mark.subprocess(env={"DD_API_KEY": "secret", "DD_SERVICE": "My Service"})
def test_agentless_request_sends_normalized_dd_service_from_environment():
    import ddtrace
    import ddtrace.internal.openfeature._agentless_source as source_mod
    from ddtrace.internal.openfeature._source_selection import create_agentless_source
    from ddtrace.internal.settings.openfeature import config as ffe_config
    from tests.openfeature.test_agentless_provider_e2e import _FakeConn
    from tests.openfeature.test_agentless_provider_e2e import _FakeResponse

    requests = []
    source_mod.get_connection = lambda url, timeout=None: _FakeConn(_FakeResponse(304), requests)

    source = create_agentless_source(ffe_config, lambda _: None)
    assert source is not None
    source.periodic()

    assert ddtrace.config.service == "My Service"
    (request,) = requests
    assert request["headers"]["DD-Service"] == "my_service"


@pytest.mark.subprocess(env={"DD_API_KEY": "secret", "DD_SERVICE": None})
def test_agentless_request_sends_inferred_service():
    """Without DD_SERVICE, the header has the inferred service. The spans use the same service."""
    import ddtrace
    import ddtrace.internal.openfeature._agentless_source as source_mod
    from ddtrace.internal.openfeature._source_selection import create_agentless_source
    from ddtrace.internal.settings.openfeature import config as ffe_config
    from tests.conftest import DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME
    from tests.openfeature.test_agentless_provider_e2e import _FakeConn
    from tests.openfeature.test_agentless_provider_e2e import _FakeResponse

    requests = []
    source_mod.get_connection = lambda url, timeout=None: _FakeConn(_FakeResponse(304), requests)

    source = create_agentless_source(ffe_config, lambda _: None)
    assert source is not None
    source.periodic()

    assert ddtrace.config.service == DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME
    (request,) = requests
    assert request["headers"]["DD-Service"] == DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME


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
