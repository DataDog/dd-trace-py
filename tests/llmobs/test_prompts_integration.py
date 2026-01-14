"""Integration tests for LLMObs.get_prompt() API.

Tests the public LLMObs interface for prompt management with HTTP mocking.
Most tests run in standalone mode (no LLMObs.enable() needed).
"""

import json
from unittest.mock import patch

import pytest

from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import INPUT_PROMPT
from ddtrace.llmobs._prompts.prompt import ManagedPrompt
from tests.utils import override_global_config


GREETING_RESPONSE = {"prompt_id": "greeting", "version": "v1", "label": "prod", "template": "Hello {{name}}!"}


@pytest.fixture(autouse=True)
def reset_llmobs():
    """Reset LLMObs state and set default config for each test."""
    if LLMObs.enabled:
        LLMObs.disable()
    LLMObs.clear_prompt_cache(l1=True, l2=True)
    LLMObs._prompt_manager = None
    LLMObs._prompt_manager_initialized = False

    with override_global_config(dict(_dd_api_key="test-key", _llmobs_ml_app="test-app")):
        yield

    if LLMObs.enabled:
        LLMObs.disable()
    LLMObs.clear_prompt_cache(l1=True, l2=True)
    LLMObs._prompt_manager = None
    LLMObs._prompt_manager_initialized = False


class MockHTTPResponse:
    def __init__(self, status: int, body: dict | str | None = None):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        if isinstance(self._body, dict):
            return json.dumps(self._body).encode("utf-8")
        return self._body.encode("utf-8") if isinstance(self._body, str) else b""


class MockHTTPConnection:
    def __init__(self, response: MockHTTPResponse):
        self._response = response
        self.request_headers = None
        self.request_path = None

    def request(self, method: str, path: str, headers: dict | None = None):
        self.request_path = path
        self.request_headers = headers

    def getresponse(self):
        return self._response

    def close(self):
        pass


def mock_api(status: int = 200, body: dict | str | None = None):
    """Create a mock API that returns the given response."""
    conn = MockHTTPConnection(MockHTTPResponse(status, body))
    return patch("ddtrace.llmobs._prompts.manager.get_connection", lambda *a, **k: conn), conn


class TestLLMObsGetPrompt:
    """Integration tests for LLMObs.get_prompt() - runs in standalone mode by default."""

    def test_basic_get_prompt(self):
        """get_prompt returns ManagedPrompt with correct properties."""
        with mock_api(200, GREETING_RESPONSE)[0]:
            prompt = LLMObs.get_prompt("greeting")

        assert isinstance(prompt, ManagedPrompt)
        assert prompt.id == "greeting"
        assert prompt.version == "v1"
        assert prompt.source == "registry"
        assert prompt.format(name="Alice") == "Hello Alice!"

    def test_env_config_flows_to_request(self):
        """Config from env vars flows to HTTP request headers and path."""
        patcher, conn = mock_api(200, GREETING_RESPONSE)
        with patcher:
            LLMObs.get_prompt("greeting")

        assert conn.request_headers["DD-API-KEY"] == "test-key"
        assert "ml_app=test-app" in conn.request_path

    def test_caching_avoids_api_call(self):
        """Second call uses L1 cache, no API call."""
        call_count = 0

        def counting_conn(*a, **k):
            nonlocal call_count
            call_count += 1
            return MockHTTPConnection(MockHTTPResponse(200, GREETING_RESPONSE))

        with patch("ddtrace.llmobs._prompts.manager.get_connection", counting_conn):
            prompt1 = LLMObs.get_prompt("greeting")
        assert call_count == 1
        assert prompt1.source == "registry"

        prompt2 = LLMObs.get_prompt("greeting")
        assert call_count == 1  # No new API call
        assert prompt2.source == "cache"

    def test_fallback_on_api_error(self):
        """Returns user fallback when API returns 500."""
        with mock_api(500, "Error")[0]:
            prompt = LLMObs.get_prompt("greeting", fallback="Default: {{name}}")

        assert prompt.source == "fallback"
        assert prompt.format(name="Bob") == "Default: Bob"

    def test_fallback_on_404(self):
        """Returns user fallback when prompt not found."""
        with mock_api(404, "Not Found")[0]:
            prompt = LLMObs.get_prompt("missing", fallback=[{"role": "user", "content": "Hi"}])

        assert prompt.source == "fallback"
        assert prompt.format() == [{"role": "user", "content": "Hi"}]

    def test_annotation_context_requires_enable(self, tracer):
        """annotation_context with spans requires LLMObs.enable()."""
        LLMObs.enable(_tracer=tracer, agentless_enabled=False)

        with mock_api(200, GREETING_RESPONSE)[0]:
            prompt = LLMObs.get_prompt("greeting")

        with LLMObs.annotation_context(prompt=prompt.to_annotation_dict(), prompt_variables={"name": "Alice"}):
            with LLMObs.llm(model_name="test-model", name="test") as span:
                prompt_data = span._get_ctx_item(INPUT_PROMPT)
                assert prompt_data["id"] == "greeting"
                assert prompt_data["variables"] == {"name": "Alice"}

    def test_callable_fallback_returns_string(self):
        """Callable fallback returning string is invoked when API fails."""
        call_count = 0

        def get_fallback():
            nonlocal call_count
            call_count += 1
            return "Lazy fallback: {{name}}"

        with mock_api(500, "Error")[0]:
            prompt = LLMObs.get_prompt("greeting", fallback=get_fallback)

        assert call_count == 1
        assert prompt.source == "fallback"
        assert prompt.format(name="Bob") == "Lazy fallback: Bob"

    def test_callable_fallback_returns_prompt_dict(self):
        """Callable fallback returning Prompt dict extracts template and version."""

        def get_fallback():
            return {"template": "From dict: {{name}}", "version": "local-v1"}

        with mock_api(500, "Error")[0]:
            prompt = LLMObs.get_prompt("greeting", fallback=get_fallback)

        assert prompt.source == "fallback"
        assert prompt.version == "local-v1"
        assert prompt.format(name="Alice") == "From dict: Alice"

    def test_callable_fallback_returns_chat_template(self):
        """Callable fallback returning Prompt dict with chat_template."""

        def get_fallback():
            return {"chat_template": [{"role": "user", "content": "Hello {{name}}"}], "version": "chat-v1"}

        with mock_api(500, "Error")[0]:
            prompt = LLMObs.get_prompt("greeting", fallback=get_fallback)

        assert prompt.source == "fallback"
        assert prompt.version == "chat-v1"
        assert prompt.format(name="World") == [{"role": "user", "content": "Hello World"}]

    def test_callable_fallback_not_called_on_success(self):
        """Callable fallback is NOT invoked when API succeeds."""
        call_count = 0

        def get_fallback():
            nonlocal call_count
            call_count += 1
            return "Should not be used"

        with mock_api(200, GREETING_RESPONSE)[0]:
            prompt = LLMObs.get_prompt("greeting", fallback=get_fallback)

        assert call_count == 0
        assert prompt.source == "registry"
        assert prompt.format(name="Alice") == "Hello Alice!"
