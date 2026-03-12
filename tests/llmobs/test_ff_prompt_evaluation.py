import json
from typing import Optional
from typing import Union
from unittest.mock import patch

import pytest

from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._prompts.manager import PromptManager
from ddtrace.llmobs._prompts.prompt import ManagedPrompt
from tests.utils import override_global_config


TEXT_PROMPT_RESPONSE = {
    "prompt_id": "greeting",
    "prompt_uuid": "prompt-uuid-123",
    "prompt_version_uuid": "version-uuid-456",
    "version": "v1",
    "label": "production",
    "template": "Hello {{name}}!",
}

FF_MANAGED_PROMPT = ManagedPrompt(
    id="greeting",
    version="ff-v1",
    label="production",
    source="registry",
    template="Hello {{name}} from FF!",
    _uuid="ff-uuid-123",
    _version_uuid="ff-version-uuid-456",
)


def _reset_prompt_state():
    if LLMObs.enabled:
        LLMObs.disable()
    LLMObs.clear_prompt_cache(hot=True, warm=True)
    LLMObs._prompt_manager = None
    LLMObs._prompt_manager_initialized = False


@pytest.fixture(autouse=True)
def reset_llmobs():
    _reset_prompt_state()
    with override_global_config(dict(_dd_api_key="test-key")):
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
        self.called = False

    def request(self, method: str, path: str, headers: Optional[dict] = None):
        self.called = True

    def getresponse(self):
        return self._response

    def close(self):
        pass


def mock_api(status: int = 200, body: Optional[Union[dict, str]] = None):
    conn = MockHTTPConnection(MockHTTPResponse(status, body))
    return patch("ddtrace.llmobs._prompts.manager.get_connection", lambda *a, **k: conn)


def _make_manager(ff_prompt_serving=False, file_cache_enabled=False):
    return PromptManager(
        api_key="test-key",
        base_url="https://api.datadoghq.com",
        file_cache_enabled=file_cache_enabled,
        ff_prompt_serving=ff_prompt_serving,
    )


class TestFFPromptEvaluation:
    """Tests for FF-backed prompt evaluation in PromptManager."""

    def test_ff_returns_prompt_when_flag_found(self):
        manager = _make_manager(ff_prompt_serving=True)
        http_called = False

        def tracking_conn(*a, **k):
            nonlocal http_called
            http_called = True
            return MockHTTPConnection(MockHTTPResponse(500, "Should not be called"))

        with patch.object(manager, "_fetch_from_ff", return_value=FF_MANAGED_PROMPT):
            with patch("ddtrace.llmobs._prompts.manager.get_connection", tracking_conn):
                prompt = manager.get_prompt("greeting", label="production")

        assert prompt.id == "greeting"
        assert prompt.version == "ff-v1"
        assert prompt.template == "Hello {{name}} from FF!"
        assert prompt._uuid == "ff-uuid-123"
        assert not http_called

    def test_ff_falls_back_to_http_when_flag_not_found(self):
        manager = _make_manager(ff_prompt_serving=True)

        with patch.object(manager, "_fetch_from_ff", return_value=None):
            with mock_api(200, TEXT_PROMPT_RESPONSE):
                prompt = manager.get_prompt("greeting", label="production")

        assert prompt.id == "greeting"
        assert prompt.version == "v1"
        assert prompt.template == "Hello {{name}}!"

    def test_ff_disabled_skips_evaluation(self):
        manager = _make_manager(ff_prompt_serving=False)
        ff_called = False

        original_fetch = manager._fetch_from_ff

        def tracking_fetch(*a, **k):
            nonlocal ff_called
            ff_called = True
            return original_fetch(*a, **k)

        with patch.object(manager, "_fetch_from_ff", side_effect=tracking_fetch):
            with mock_api(200, TEXT_PROMPT_RESPONSE):
                prompt = manager.get_prompt("greeting", label="production")

        assert prompt.id == "greeting"
        assert prompt.version == "v1"
        assert not ff_called

    def test_ff_prompt_populates_caches(self):
        manager = _make_manager(ff_prompt_serving=True)

        with patch.object(manager, "_fetch_from_ff", return_value=FF_MANAGED_PROMPT):
            prompt1 = manager.get_prompt("greeting", label="production")

        assert prompt1.id == "greeting"
        assert prompt1.version == "ff-v1"

        # Second call should return from cache without calling FF
        prompt2 = manager.get_prompt("greeting", label="production")
        assert prompt2.id == "greeting"
        assert prompt2.source == "cache"

    def test_background_refresh_uses_ff(self):
        manager = _make_manager(ff_prompt_serving=True)
        http_called = False

        def tracking_conn(*a, **k):
            nonlocal http_called
            http_called = True
            return MockHTTPConnection(MockHTTPResponse(200, TEXT_PROMPT_RESPONSE))

        with patch.object(manager, "_fetch_from_ff", return_value=FF_MANAGED_PROMPT):
            with patch("ddtrace.llmobs._prompts.manager.get_connection", tracking_conn):
                manager._background_refresh("greeting:production", "greeting", "production")

        assert not http_called

    def test_background_refresh_falls_back_to_http_when_ff_fails(self):
        manager = _make_manager(ff_prompt_serving=True)

        with patch.object(manager, "_fetch_from_ff", return_value=None):
            with mock_api(200, TEXT_PROMPT_RESPONSE):
                manager._background_refresh("greeting:production", "greeting", "production")

        # Verify cache was populated via HTTP fallback
        prompt = manager.get_prompt("greeting", label="production")
        assert prompt.source == "cache"
        assert prompt.version == "v1"
