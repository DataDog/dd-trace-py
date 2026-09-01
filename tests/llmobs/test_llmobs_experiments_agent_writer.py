import mock
import pytest

from ddtrace.internal.evp_proxy.constants import EVP_NEEDS_APP_KEY_HEADER_NAME
from ddtrace.internal.evp_proxy.constants import EVP_PROXY_AGENT_BASE_PATH
from ddtrace.internal.evp_proxy.constants import EVP_SUBDOMAIN_HEADER_NAME
from ddtrace.llmobs._writer import LLMObsExperimentsClient


PUBLISH_EVALUATOR_PATH = "/api/unstable/llm-obs/v1/config/evaluators/custom"


def _client(is_agentless):
    return LLMObsExperimentsClient(
        interval=1,
        timeout=1,
        is_agentless=is_agentless,
        _api_key="test-api-key",
        _app_key="test-app-key",
    )


@pytest.fixture
def mock_conn():
    with mock.patch("ddtrace.llmobs._writer.HTTPConnection") as conn_cls:
        conn = conn_cls.return_value
        conn.getresponse.return_value = mock.Mock(status=200, **{"read.return_value": b"{}"})
        yield conn


def test_agentless_sends_both_credentials():
    assert _client(True)._auth_headers() == {"DD-API-KEY": "test-api-key", "DD-APPLICATION-KEY": "test-app-key"}


def test_agent_proxy_asks_agent_for_credentials():
    """The agent attaches both keys itself, so sending ours would leak them through the proxy."""
    headers = _client(False)._auth_headers()
    assert headers == {EVP_SUBDOMAIN_HEADER_NAME: "api", EVP_NEEDS_APP_KEY_HEADER_NAME: "true"}


def test_publish_custom_evaluator_through_agent_proxy(mock_conn):
    _client(False).publish_custom_evaluator({"eval_name": "my-eval"})

    method, path, _body, headers = mock_conn.request.call_args[0]
    assert method == "PUT"
    assert path == "{}{}".format(EVP_PROXY_AGENT_BASE_PATH, PUBLISH_EVALUATOR_PATH)
    assert headers[EVP_SUBDOMAIN_HEADER_NAME] == "api"
    assert headers[EVP_NEEDS_APP_KEY_HEADER_NAME] == "true"
    assert "DD-API-KEY" not in headers
    assert "DD-APPLICATION-KEY" not in headers


def test_publish_custom_evaluator_agentless_unchanged(mock_conn):
    _client(True).publish_custom_evaluator({"eval_name": "my-eval"})

    method, path, _body, headers = mock_conn.request.call_args[0]
    assert method == "PUT"
    assert path == PUBLISH_EVALUATOR_PATH
    assert headers["DD-API-KEY"] == "test-api-key"
    assert headers["DD-APPLICATION-KEY"] == "test-app-key"
    assert EVP_SUBDOMAIN_HEADER_NAME not in headers


def test_multipart_request_through_agent_proxy(mock_conn):
    """Bulk CSV upload shares the credential swap; without it the proxy would reject the upload."""
    _client(False).multipart_request("POST", "/api/unstable/upload", "multipart/form-data", b"payload")

    _method, _path, _body, headers = mock_conn.request.call_args[0]
    assert headers[EVP_SUBDOMAIN_HEADER_NAME] == "api"
    assert headers[EVP_NEEDS_APP_KEY_HEADER_NAME] == "true"
    assert "DD-API-KEY" not in headers
    assert "DD-APPLICATION-KEY" not in headers


@pytest.mark.parametrize(
    "app_key,proxy_available,expected_agentless",
    [
        # An app key of our own means intake can authenticate us directly.
        ("test-app-key", True, True),
        ("test-app-key", False, True),
        # No app key, but an agent that can supply one.
        ("", True, False),
        # No app key and no proxy to borrow one from, so there is nothing to fall back to.
        ("", False, True),
    ],
)
def test_experiments_client_mode_selection(app_key, proxy_available, expected_agentless):
    from ddtrace.llmobs._llmobs import LLMObs

    original_app_key = LLMObs._app_key
    LLMObs._app_key = app_key
    try:
        with mock.patch("ddtrace.llmobs._llmobs.should_use_agentless", return_value=not proxy_available):
            instance = LLMObs()
        assert instance._dne_client._agentless is expected_agentless
    finally:
        LLMObs._app_key = original_app_key


def test_override_origin_stays_direct(monkeypatch):
    """An override origin replaces intake, not the agent.

    Proxying it would send /evp_proxy/v2-prefixed paths to a server that supplies no credentials.
    """
    from ddtrace.llmobs._llmobs import LLMObs

    monkeypatch.setenv("DD_LLMOBS_OVERRIDE_ORIGIN", "http://localhost:1234")
    original_app_key = LLMObs._app_key
    LLMObs._app_key = ""
    try:
        with mock.patch("ddtrace.llmobs._llmobs.should_use_agentless", return_value=False):
            client = LLMObs()._dne_client
        assert client._agentless is True
        assert client._intake == "http://localhost:1234"
        assert EVP_PROXY_AGENT_BASE_PATH not in client._endpoint
        assert "DD-API-KEY" in client._auth_headers()
    finally:
        LLMObs._app_key = original_app_key
