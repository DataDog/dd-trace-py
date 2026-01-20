import json
from typing import Optional
from typing import Union
from unittest.mock import patch

import pytest

from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import INPUT_PROMPT
from ddtrace.llmobs._prompts.prompt import ManagedPrompt
from tests.utils import override_global_config


# Mock API responses
TEXT_PROMPT_RESPONSE = {
    "prompt_id": "greeting",
    "version": "v1",
    "label": "prod",
    "template": "Hello {name}!",
}

CHAT_PROMPT_RESPONSE = {
    "prompt_id": "assistant",
    "version": "v2",
    "label": "prod",
    "template": [
        {"role": "system", "content": "You are {persona}."},
        {"role": "user", "content": "{question}"},
    ],
}

DEV_PROMPT_RESPONSE = {
    "prompt_id": "greeting",
    "version": "dev-v1",
    "label": "dev",
    "template": "DEBUG: Hello {name}!",
}


def _reset_prompt_state():
    """Reset LLMObs prompt manager state."""
    if LLMObs.enabled:
        LLMObs.disable()
    LLMObs.clear_prompt_cache(l1=True, l2=True)
    LLMObs._prompt_manager = None
    LLMObs._prompt_manager_initialized = False


@pytest.fixture(autouse=True)
def reset_llmobs():
    """Reset LLMObs state for each test."""
    _reset_prompt_state()

    with override_global_config(dict(_dd_api_key="test-key", _llmobs_ml_app="test-app")):
        yield

    _reset_prompt_state()


class MockHTTPResponse:
    def __init__(self, status: int, body: Optional[Union[dict, str]] = None):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        if isinstance(self._body, dict):
            return json.dumps(self._body).encode("utf-8")
        return self._body.encode("utf-8") if isinstance(self._body, str) else b""


class MockHTTPConnection:
    def __init__(self, response: MockHTTPResponse):
        self._response = response

    def request(self, method: str, path: str, headers: Optional[dict] = None):
        pass

    def getresponse(self):
        return self._response

    def close(self):
        pass


def mock_api(status: int = 200, body: Optional[Union[dict, str]] = None):
    """Create a mock API returning the given response."""
    conn = MockHTTPConnection(MockHTTPResponse(status, body))
    return patch("ddtrace.llmobs._prompts.manager.get_connection", lambda *a, **k: conn)


class TestGetPrompt:
    """Core get_prompt functionality - customer use cases."""

    def test_fetch_and_render_text_prompt(self):
        """Fetch a text prompt from registry and render with variables."""
        with mock_api(200, TEXT_PROMPT_RESPONSE):
            prompt = LLMObs.get_prompt("greeting")

        assert isinstance(prompt, ManagedPrompt)
        assert prompt.id == "greeting"
        assert prompt.version == "v1"
        assert prompt.source == "registry"
        assert prompt.format(name="Alice") == "Hello Alice!"

    def test_fetch_and_render_chat_prompt(self):
        """Fetch a chat template and render as messages."""
        with mock_api(200, CHAT_PROMPT_RESPONSE):
            prompt = LLMObs.get_prompt("assistant")

        messages = prompt.format(persona="helpful assistant", question="What is Python?")
        assert isinstance(messages, list)
        assert len(messages) == 2
        assert messages[0]["content"] == "You are helpful assistant."
        assert messages[1]["content"] == "What is Python?"

    def test_caching_returns_from_cache(self):
        """Second call returns cached prompt without API call."""
        call_count = 0

        def counting_conn(*a, **k):
            nonlocal call_count
            call_count += 1
            return MockHTTPConnection(MockHTTPResponse(200, TEXT_PROMPT_RESPONSE))

        with patch("ddtrace.llmobs._prompts.manager.get_connection", counting_conn):
            prompt1 = LLMObs.get_prompt("greeting")
        assert call_count == 1
        assert prompt1.source == "registry"

        # Second call - no API request
        prompt2 = LLMObs.get_prompt("greeting")
        assert call_count == 1
        assert prompt2.source == "cache"

    def test_label_parameter(self):
        """Different labels fetch different prompt versions."""
        # Fetch prod version
        with mock_api(200, TEXT_PROMPT_RESPONSE):
            prod_prompt = LLMObs.get_prompt("greeting", label="prod")
        assert prod_prompt.version == "v1"
        assert prod_prompt.label == "prod"

        # Clear cache to force new fetch
        LLMObs.clear_prompt_cache(l1=True, l2=True)

        # Fetch dev version
        with mock_api(200, DEV_PROMPT_RESPONSE):
            dev_prompt = LLMObs.get_prompt("greeting", label="dev")
        assert dev_prompt.version == "dev-v1"
        assert dev_prompt.label == "dev"
        assert "DEBUG" in dev_prompt.format(name="Test")


class TestFallback:
    """Fallback behavior when API unavailable."""

    def test_string_fallback_on_error(self):
        """String fallback used when API returns 500."""
        with mock_api(500, "Internal Server Error"):
            prompt = LLMObs.get_prompt("greeting", fallback="Default: {name}")

        assert prompt.source == "fallback"
        assert prompt.format(name="Bob") == "Default: Bob"

    def test_chat_fallback_on_404(self):
        """Chat template fallback when prompt not found."""
        with mock_api(404, "Not Found"):
            prompt = LLMObs.get_prompt("missing", fallback=[{"role": "user", "content": "Hi {name}"}])

        assert prompt.source == "fallback"
        assert prompt.format(name="Alice") == [{"role": "user", "content": "Hi Alice"}]

    def test_callable_fallback_lazy(self):
        """Callable fallback only invoked when API fails."""
        call_count = 0

        def get_fallback():
            nonlocal call_count
            call_count += 1
            return {"template": "Lazy: {name}", "version": "local-v1"}

        with mock_api(500, "Error"):
            prompt = LLMObs.get_prompt("greeting", fallback=get_fallback)

        assert call_count == 1
        assert prompt.source == "fallback"
        assert prompt.version == "local-v1"
        assert prompt.format(name="Bob") == "Lazy: Bob"

    def test_callable_fallback_not_called_on_success(self):
        """Callable fallback NOT invoked when API succeeds."""
        call_count = 0

        def get_fallback():
            nonlocal call_count
            call_count += 1
            return "Should not be used"

        with mock_api(200, TEXT_PROMPT_RESPONSE):
            prompt = LLMObs.get_prompt("greeting", fallback=get_fallback)

        assert call_count == 0
        assert prompt.source == "registry"


class TestCacheControl:
    """Explicit cache management APIs."""

    def test_clear_prompt_cache(self):
        """clear_prompt_cache() removes cached prompts."""
        # Populate cache
        with mock_api(200, TEXT_PROMPT_RESPONSE):
            prompt1 = LLMObs.get_prompt("greeting")
        assert prompt1.source == "registry"

        # Verify cached
        prompt2 = LLMObs.get_prompt("greeting")
        assert prompt2.source == "cache"

        # Clear and verify re-fetch required
        LLMObs.clear_prompt_cache(l1=True, l2=True)

        with mock_api(200, TEXT_PROMPT_RESPONSE):
            prompt3 = LLMObs.get_prompt("greeting")
        assert prompt3.source == "registry"

    def test_refresh_prompt(self):
        """refresh_prompt() forces re-fetch from API."""
        # Initial fetch
        with mock_api(200, TEXT_PROMPT_RESPONSE):
            prompt1 = LLMObs.get_prompt("greeting")
        assert prompt1.version == "v1"

        # Update response for refresh
        updated_response = {**TEXT_PROMPT_RESPONSE, "version": "v2"}

        # Refresh forces new fetch
        with mock_api(200, updated_response):
            refreshed = LLMObs.refresh_prompt("greeting")

        assert refreshed is not None
        assert refreshed.version == "v2"

        # Subsequent get returns refreshed version
        prompt2 = LLMObs.get_prompt("greeting")
        assert prompt2.version == "v2"

    def test_refresh_prompt_evicts_on_404(self):
        """refresh_prompt() evicts cache when prompt is deleted (404)."""
        # Initial fetch and cache
        with mock_api(200, TEXT_PROMPT_RESPONSE):
            prompt1 = LLMObs.get_prompt("greeting")
        assert prompt1.source == "registry"

        # Verify cached
        prompt2 = LLMObs.get_prompt("greeting")
        assert prompt2.source == "cache"

        # Prompt is deleted from registry (404)
        with mock_api(404, "Not Found"):
            result = LLMObs.refresh_prompt("greeting")
        assert result is None

        # Cache should be evicted, next call uses fallback
        with mock_api(404, "Not Found"):
            prompt3 = LLMObs.get_prompt("greeting", fallback="Fallback: {name}")
        assert prompt3.source == "fallback"
        assert prompt3.format(name="Bob") == "Fallback: Bob"


class TestAnnotationContext:
    """Integration with LLMObs observability."""

    def test_annotation_context_captures_variables(self, tracer):
        """Variables passed to to_annotation_dict appear on span."""
        LLMObs.enable(_tracer=tracer, agentless_enabled=False)

        with mock_api(200, TEXT_PROMPT_RESPONSE):
            prompt = LLMObs.get_prompt("greeting")

        with LLMObs.annotation_context(prompt=prompt.to_annotation_dict(name="Alice")):
            with LLMObs.llm(model_name="test-model", name="test") as span:
                prompt_data = span._get_ctx_item(INPUT_PROMPT)
                assert prompt_data["id"] == "greeting"
                assert prompt_data["version"] == "v1"
                assert prompt_data["variables"] == {"name": "Alice"}
