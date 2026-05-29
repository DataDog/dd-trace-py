import json
import os
from typing import Optional
from typing import Union
from unittest.mock import patch
import warnings

import pytest

from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import FFEvalState
from ddtrace.llmobs._prompts.manager import PromptManager
from ddtrace.llmobs._prompts.prompt import ManagedPrompt
from ddtrace.llmobs._utils import get_llmobs_input_prompt
from ddtrace.llmobs.types import PromptProviderNotReady
from tests.utils import override_global_config


# Mock API responses
TEXT_PROMPT_RESPONSE = {
    "prompt_id": "greeting",
    "prompt_uuid": "prompt-uuid-123",
    "prompt_version_uuid": "version-uuid-456",
    "version": "v1",
    "label": "production",
    "template": "Hello {name}!",
}

CHAT_PROMPT_RESPONSE = {
    "prompt_id": "assistant",
    "prompt_uuid": "chat-uuid-123",
    "prompt_version_uuid": "chat-version-uuid-456",
    "version": "v2",
    "label": "production",
    "template": [
        {"role": "system", "content": "You are {{persona}}."},
        {"role": "user", "content": "{{question}}"},
    ],
}

DEV_PROMPT_RESPONSE = {
    "prompt_id": "greeting",
    "prompt_uuid": "dev-prompt-uuid-789",
    "prompt_version_uuid": "dev-version-uuid-012",
    "version": "dev-v1",
    "label": "development",
    "template": "DEBUG: Hello {name}!",
}


def _reset_prompt_state():
    """Reset LLMObs prompt manager state."""
    if LLMObs.enabled:
        LLMObs.disable()
    LLMObs.clear_prompt_cache(hot=True, warm=True)
    LLMObs._prompt_manager = None
    LLMObs._prompt_manager_initialized = False


@pytest.fixture(autouse=True)
def reset_llmobs():
    """Reset LLMObs state for each test."""
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


def assert_prompt_matches_response(prompt, response, expected_source):
    """Assert prompt fields match the API response."""
    assert prompt.id == response["prompt_id"]
    assert prompt.version == response["version"]
    assert prompt.source == expected_source
    assert prompt._uuid == response.get("prompt_uuid")
    assert prompt._version_uuid == response.get("prompt_version_uuid")


class TestPrompts:
    """Tests for the Managed Prompt Registry SDK."""

    def test_fetch_and_render_text_prompt(self):
        """Fetch a text prompt from registry and render with variables."""
        with mock_api(200, TEXT_PROMPT_RESPONSE):
            prompt = LLMObs.get_prompt("greeting")

        assert isinstance(prompt, ManagedPrompt)
        assert_prompt_matches_response(prompt, TEXT_PROMPT_RESPONSE, "registry")
        assert prompt.format(name="Alice") == "Hello Alice!"

    def test_fetch_and_render_chat_prompt(self):
        """Fetch a chat template and render as messages."""
        with mock_api(200, CHAT_PROMPT_RESPONSE):
            prompt = LLMObs.get_prompt("assistant")

        assert_prompt_matches_response(prompt, CHAT_PROMPT_RESPONSE, "registry")
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
        assert_prompt_matches_response(prompt1, TEXT_PROMPT_RESPONSE, "registry")

        prompt2 = LLMObs.get_prompt("greeting")
        assert call_count == 1
        assert_prompt_matches_response(prompt2, TEXT_PROMPT_RESPONSE, "cache")

    def test_cache_ttl_zero_disables_cache(self):
        """When cache TTL is zero, prompts are fetched from registry on every call."""
        call_count = 0

        def counting_conn(*a, **k):
            nonlocal call_count
            call_count += 1
            return MockHTTPConnection(MockHTTPResponse(200, TEXT_PROMPT_RESPONSE))

        with patch.dict(os.environ, {"DD_LLMOBS_PROMPTS_CACHE_TTL": "0"}):
            with patch("ddtrace.llmobs._prompts.manager.get_connection", counting_conn):
                prompt1 = LLMObs.get_prompt("greeting")
                prompt2 = LLMObs.get_prompt("greeting")

        assert call_count == 2
        assert_prompt_matches_response(prompt1, TEXT_PROMPT_RESPONSE, "registry")
        assert_prompt_matches_response(prompt2, TEXT_PROMPT_RESPONSE, "registry")

    def test_label_parameter(self):
        """Different labels fetch different prompt versions."""
        with mock_api(200, TEXT_PROMPT_RESPONSE):
            prod_prompt = LLMObs.get_prompt("greeting", label="production")
        assert prod_prompt.version == "v1"
        assert prod_prompt.label == "production"

        LLMObs.clear_prompt_cache(hot=True, warm=True)

        with mock_api(200, DEV_PROMPT_RESPONSE):
            dev_prompt = LLMObs.get_prompt("greeting", label="development")
        assert dev_prompt.version == "dev-v1"
        assert dev_prompt.label == "development"
        assert "DEBUG" in dev_prompt.format(name="Test")

    def test_string_fallback_on_error(self):
        """String fallback used when API returns 500."""
        with mock_api(500, "Internal Server Error"):
            prompt = LLMObs.get_prompt("greeting", fallback="Default: {name}")

        assert prompt.source == "fallback"
        assert prompt._uuid is None
        assert prompt._version_uuid is None
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

    def test_raises_when_no_fallback_provided(self):
        """Raises ValueError when API fails and no fallback is provided."""
        with mock_api(500, "Internal Server Error"):
            with pytest.raises(ValueError) as exc_info:
                LLMObs.get_prompt("greeting")
        assert "could not be fetched and no fallback was provided" in str(exc_info.value)
        assert "Internal Server Error" in str(exc_info.value)

    def test_raises_with_404_detail_when_no_fallback_provided(self):
        detail = "prompt 'support-assistant' exists but label 'production' was not found"
        with mock_api(404, {"detail": detail}):
            with pytest.raises(ValueError) as exc_info:
                LLMObs.get_prompt("support-assistant", label="production")
        assert "could not be fetched and no fallback was provided" in str(exc_info.value)
        assert detail in str(exc_info.value)

    def test_clear_prompt_cache(self):
        """clear_prompt_cache() removes cached prompts."""
        with mock_api(200, TEXT_PROMPT_RESPONSE):
            prompt1 = LLMObs.get_prompt("greeting")
        assert prompt1.source == "registry"

        prompt2 = LLMObs.get_prompt("greeting")
        assert prompt2.source == "cache"

        LLMObs.clear_prompt_cache(hot=True, warm=True)

        with mock_api(200, TEXT_PROMPT_RESPONSE):
            prompt3 = LLMObs.get_prompt("greeting")
        assert prompt3.source == "registry"

    def test_refresh_prompt(self):
        """refresh_prompt() forces re-fetch from API."""
        with mock_api(200, TEXT_PROMPT_RESPONSE):
            prompt1 = LLMObs.get_prompt("greeting")
        assert prompt1.version == "v1"

        updated_response = {**TEXT_PROMPT_RESPONSE, "version": "v2"}

        with mock_api(200, updated_response):
            refreshed = LLMObs.refresh_prompt("greeting")

        assert refreshed is not None
        assert refreshed.version == "v2"

        prompt2 = LLMObs.get_prompt("greeting")
        assert prompt2.version == "v2"

    def test_refresh_prompt_evicts_on_404(self):
        """refresh_prompt() evicts cache when prompt is deleted (404)."""
        with mock_api(200, TEXT_PROMPT_RESPONSE):
            prompt1 = LLMObs.get_prompt("greeting")
        assert prompt1.source == "registry"

        prompt2 = LLMObs.get_prompt("greeting")
        assert prompt2.source == "cache"

        with mock_api(404, "Not Found"):
            result = LLMObs.refresh_prompt("greeting")
        assert result is None

        with mock_api(404, "Not Found"):
            prompt3 = LLMObs.get_prompt("greeting", fallback="Fallback: {name}")
        assert prompt3.source == "fallback"
        assert prompt3.format(name="Bob") == "Fallback: Bob"

    def test_annotation_context_captures_variables(self, tracer):
        """Variables passed to to_annotation_dict appear on span."""
        LLMObs.enable(_tracer=tracer, agentless_enabled=False)

        with mock_api(200, TEXT_PROMPT_RESPONSE):
            prompt = LLMObs.get_prompt("greeting")

        with LLMObs.annotation_context(prompt=prompt.to_annotation_dict(name="Alice")):
            with LLMObs.llm(model_name="test-model", name="test") as span:
                prompt_data = get_llmobs_input_prompt(span)
                assert prompt_data["id"] == "greeting"
                assert prompt_data["version"] == "v1"
                assert prompt_data["label"] == "production"
                assert prompt_data["variables"] == {"name": "Alice"}
                assert prompt_data["prompt_uuid"] == "prompt-uuid-123"
                assert prompt_data["prompt_version_uuid"] == "version-uuid-456"
                assert "tags" not in prompt_data

    def test_annotation_context_fallback_prompt_omits_label_and_tags(self, tracer):
        """Fallback managed prompt should annotate without managed-prompt tags."""
        LLMObs.enable(_tracer=tracer, agentless_enabled=False)

        with mock_api(500, "Error"):
            prompt = LLMObs.get_prompt("greeting", fallback="Fallback: {name}")

        with LLMObs.annotation_context(prompt=prompt.to_annotation_dict(name="Alice")):
            with LLMObs.llm(model_name="test-model", name="test") as span:
                prompt_data = get_llmobs_input_prompt(span)
                assert prompt_data["id"] == "greeting"
                assert prompt_data["version"] == "fallback"
                assert prompt_data["variables"] == {"name": "Alice"}
                assert prompt_data["template"] == "Fallback: {name}"
                assert "label" not in prompt_data
                assert "tags" not in prompt_data

    def test_trigger_background_refresh_does_not_leave_stale_thread_entry(self):
        manager = PromptManager(api_key="test-key", base_url="https://api.datadoghq.com", file_cache_enabled=False)

        class ImmediateThread:
            def __init__(self, target=None, daemon=None):
                self._target = target
                self.daemon = daemon

            def start(self):
                if self._target is not None:
                    self._target()

            def join(self, timeout=None):
                pass

        with patch.object(manager, "_background_refresh", return_value=None) as refresh_mock:
            with patch("ddtrace.llmobs._prompts.manager.threading.Thread", ImmediateThread):
                manager._trigger_background_refresh("greeting:production", "greeting", "production")
                assert "greeting:production" not in manager._refresh_threads
                manager._trigger_background_refresh("greeting:production", "greeting", "production")

        assert refresh_mock.call_count == 2


class TestPromptRouting:
    """Tests for the routing demux logic."""

    def _make_manager(self, agentless=True):
        return PromptManager(
            api_key="test-key",
            base_url="https://api.datadoghq.com",
            file_cache_enabled=False,
            agentless=agentless,
        )

    def test_label_routes_to_http(self):
        manager = self._make_manager(agentless=False)
        with mock_api(200, TEXT_PROMPT_RESPONSE):
            with patch.object(manager, "_fetch_from_ff") as ff_mock:
                prompt = manager.get_prompt("greeting", label="production")
        ff_mock.assert_not_called()
        assert prompt.source == "registry"

    def test_no_label_no_env_routes_to_http(self):
        manager = self._make_manager(agentless=False)
        with mock_api(200, TEXT_PROMPT_RESPONSE):
            with patch.object(manager, "_fetch_from_ff") as ff_mock:
                with patch("ddtrace.llmobs._prompts.manager.config") as cfg:
                    cfg.env = None
                    prompt = manager.get_prompt("greeting")
        ff_mock.assert_not_called()
        assert prompt.source == "registry"

    def test_no_label_with_env_agent_mode_routes_to_ff(self):
        manager = self._make_manager(agentless=False)
        ff_prompt = ManagedPrompt(
            id="greeting",
            version="ff-v1",
            label="production",
            source="ff",
            template="FF Hello!",
            _uuid="u1",
            _version_uuid="v1",
        )
        with patch.object(manager, "_fetch_from_ff", return_value=(ff_prompt, FFEvalState.FF)) as ff_mock:
            with patch("ddtrace.llmobs._prompts.manager.config") as cfg:
                cfg.env = "staging"
                prompt = manager.get_prompt("greeting")
        ff_mock.assert_called_once_with("greeting", None, {})
        assert prompt.source == "ff"

    def test_no_label_with_env_agentless_routes_to_http(self):
        manager = self._make_manager(agentless=True)
        with mock_api(200, TEXT_PROMPT_RESPONSE):
            with patch.object(manager, "_fetch_from_ff") as ff_mock:
                with patch("ddtrace.llmobs._prompts.manager.config") as cfg:
                    cfg.env = "production"
                    prompt = manager.get_prompt("greeting")
        ff_mock.assert_not_called()
        assert prompt.source == "registry"

    def test_ff_no_flag_falls_back_to_http(self):
        manager = self._make_manager(agentless=False)
        with mock_api(200, TEXT_PROMPT_RESPONSE):
            with patch.object(manager, "_fetch_from_ff", return_value=(None, FFEvalState.NO_FLAG)):
                with patch("ddtrace.llmobs._prompts.manager.config") as cfg:
                    cfg.env = "staging"
                    prompt = manager.get_prompt("greeting")
        assert prompt.source == "registry"

    def test_targeting_key_passed_to_ff(self):
        manager = self._make_manager(agentless=False)
        ff_prompt = ManagedPrompt(
            id="greeting",
            version="ff-v1",
            label=None,
            source="ff",
            template="Hello!",
        )
        with patch.object(manager, "_fetch_from_ff", return_value=(ff_prompt, FFEvalState.FF)) as ff_mock:
            with patch("ddtrace.llmobs._prompts.manager.config") as cfg:
                cfg.env = "staging"
                manager.get_prompt("greeting", targeting_key="user-123", tier="premium")
        ff_mock.assert_called_once_with("greeting", "user-123", {"tier": "premium"})

    def test_mixing_warning_label_with_targeting_key(self):
        manager = self._make_manager()
        with mock_api(200, TEXT_PROMPT_RESPONSE):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                manager.get_prompt("greeting", label="production", targeting_key="user-123")
        assert len(w) == 1
        assert "label" in str(w[0].message)
        assert "targeting" in str(w[0].message).lower()
        assert w[0].category == UserWarning


class _StubDetails:
    def __init__(self, value, error_code=None):
        self.value = value
        self.error_code = error_code


class _StubClient:
    def __init__(self, details=None, fire_ready=False):
        self._details = details
        self._fire_ready = fire_ready

    def get_object_details(self, flag_key, default, context):
        return self._details

    def add_handler(self, event, handler):
        if self._fire_ready:
            handler(None)

    def remove_handler(self, event, handler):
        pass


class TestPromptNotReady:
    """get_prompt behavior when the FFE provider has not received its first RC payload."""

    def _make_manager(self):
        return PromptManager(
            api_key="test-key",
            base_url="https://api.datadoghq.com",
            file_cache_enabled=False,
            agentless=False,
        )

    def test_not_ready_with_fallback_returns_fallback_not_http(self):
        manager = self._make_manager()
        with patch.object(manager, "_fetch_from_ff", return_value=(None, FFEvalState.NOT_READY)):
            with patch.object(manager, "_get_prompt_http") as http_mock:
                with patch("ddtrace.llmobs._prompts.manager.config") as cfg:
                    cfg.env = "staging"
                    prompt = manager.get_prompt("greeting", fallback="Hi {{user}}")
        http_mock.assert_not_called()
        assert prompt.source == "fallback"

    def test_not_ready_without_fallback_raises_not_http(self):
        manager = self._make_manager()
        with patch.object(manager, "_fetch_from_ff", return_value=(None, FFEvalState.NOT_READY)):
            with patch.object(manager, "_get_prompt_http") as http_mock:
                with patch("ddtrace.llmobs._prompts.manager.config") as cfg:
                    cfg.env = "staging"
                    with pytest.raises(ValueError) as exc:
                        manager.get_prompt("greeting")
        assert isinstance(exc.value, PromptProviderNotReady)
        http_mock.assert_not_called()


class TestFetchFromFFStateMapping:
    """_fetch_from_ff maps OpenFeature evaluation outcomes to routing states."""

    def _make_manager(self):
        return PromptManager(
            api_key="test-key",
            base_url="https://api.datadoghq.com",
            file_cache_enabled=False,
            agentless=False,
        )

    def _run(self, manager, details=None):
        import ddtrace.internal.settings.openfeature as ffe_settings

        with patch.object(ffe_settings.config, "experimental_flagging_provider_enabled", True):
            with patch.object(manager, "_ensure_ffe_rc"), patch.object(manager, "_ensure_ffe_provider"):
                with patch("openfeature.api.get_client", return_value=_StubClient(details=details)):
                    return manager._fetch_from_ff("greeting", None, {})

    def test_disabled_when_flag_off(self):
        import ddtrace.internal.settings.openfeature as ffe_settings

        manager = self._make_manager()
        with patch.object(ffe_settings.config, "experimental_flagging_provider_enabled", False):
            prompt, state = manager._fetch_from_ff("greeting", None, {})
        assert (prompt, state) == (None, FFEvalState.DISABLED)

    def test_provider_not_ready(self):
        pytest.importorskip("openfeature")
        from openfeature.exception import ErrorCode

        manager = self._make_manager()
        prompt, state = self._run(manager, _StubDetails(value={}, error_code=ErrorCode.PROVIDER_NOT_READY))
        assert (prompt, state) == (None, FFEvalState.NOT_READY)

    def test_flag_not_found_is_no_flag(self):
        pytest.importorskip("openfeature")
        from openfeature.exception import ErrorCode

        manager = self._make_manager()
        prompt, state = self._run(manager, _StubDetails(value={}, error_code=ErrorCode.FLAG_NOT_FOUND))
        assert (prompt, state) == (None, FFEvalState.NO_FLAG)

    def test_valid_variant_is_ff(self):
        pytest.importorskip("openfeature")

        manager = self._make_manager()
        value = {"prompt_id": "greeting", "version": "3", "template": "Hello!"}
        prompt, state = self._run(manager, _StubDetails(value=value, error_code=None))
        assert state == FFEvalState.FF
        assert prompt.source == "ff"
        assert prompt.version == "3"


class TestWaitForReady:
    def _make_manager(self, agentless=False):
        return PromptManager(
            api_key="test-key",
            base_url="https://api.datadoghq.com",
            file_cache_enabled=False,
            agentless=agentless,
        )

    def test_returns_false_when_agentless(self):
        manager = self._make_manager(agentless=True)
        with patch("ddtrace.llmobs._prompts.manager.config") as cfg:
            cfg.env = "staging"
            assert manager.wait_for_ready(0.1) is False

    def test_returns_false_when_no_env(self):
        manager = self._make_manager()
        with patch("ddtrace.llmobs._prompts.manager.config") as cfg:
            cfg.env = None
            assert manager.wait_for_ready(0.1) is False

    def test_returns_false_when_disabled(self):
        import ddtrace.internal.settings.openfeature as ffe_settings

        manager = self._make_manager()
        with patch("ddtrace.llmobs._prompts.manager.config") as cfg:
            cfg.env = "staging"
            with patch.object(ffe_settings.config, "experimental_flagging_provider_enabled", False):
                assert manager.wait_for_ready(0.1) is False

    def test_returns_true_when_ready(self):
        pytest.importorskip("openfeature")
        import ddtrace.internal.settings.openfeature as ffe_settings

        manager = self._make_manager()
        with patch("ddtrace.llmobs._prompts.manager.config") as cfg:
            cfg.env = "staging"
            with patch.object(ffe_settings.config, "experimental_flagging_provider_enabled", True):
                with patch.object(manager, "_ensure_ffe_rc"), patch.object(manager, "_ensure_ffe_provider"):
                    with patch("openfeature.api.get_client", return_value=_StubClient(fire_ready=True)):
                        assert manager.wait_for_ready(5.0) is True

    def test_returns_false_on_timeout(self):
        pytest.importorskip("openfeature")
        import ddtrace.internal.settings.openfeature as ffe_settings

        manager = self._make_manager()
        with patch("ddtrace.llmobs._prompts.manager.config") as cfg:
            cfg.env = "staging"
            with patch.object(ffe_settings.config, "experimental_flagging_provider_enabled", True):
                with patch.object(manager, "_ensure_ffe_rc"), patch.object(manager, "_ensure_ffe_provider"):
                    with patch("openfeature.api.get_client", return_value=_StubClient(fire_ready=False)):
                        assert manager.wait_for_ready(0.1) is False
