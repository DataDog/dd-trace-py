from contextlib import contextmanager
import json
import os
from typing import Optional
from typing import Union
from unittest.mock import patch
import warnings

import pytest

from ddtrace import config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._integrations import BaseLLMIntegration
from ddtrace.llmobs._prompts.cache import WarmCache
from ddtrace.llmobs._prompts.manager import PromptManager
from ddtrace.llmobs._prompts.manager import _PromptRequest
from ddtrace.llmobs._prompts.prompt import ManagedPrompt
from ddtrace.llmobs._utils import get_llmobs_input_prompt
from ddtrace.llmobs.types import PromptAPIError
from ddtrace.llmobs.types import PromptAuthError
from ddtrace.llmobs.types import PromptConflictError
from ddtrace.llmobs.types import PromptNotFoundError
from ddtrace.llmobs.types import PromptServerError
from ddtrace.llmobs.types import PromptValidationError
from tests.utils import override_global_config


# Mock API responses
TEXT_PROMPT_RESPONSE = {
    "prompt_id": "greeting",
    "prompt_uuid": "prompt-uuid-123",
    "prompt_version_uuid": "version-uuid-456",
    "version": "v1",
    "labels": ["development", "production"],
    "template": "Hello {name}!",
}

CHAT_PROMPT_RESPONSE = {
    "prompt_id": "assistant",
    "prompt_uuid": "chat-uuid-123",
    "prompt_version_uuid": "chat-version-uuid-456",
    "version": "v2",
    "labels": ["production"],
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
    "labels": ["development"],
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
        if isinstance(self._body, str):
            return self._body.encode("utf-8")
        if self._body is None:
            return b""
        return json.dumps(self._body).encode("utf-8")


class MockHTTPConnection:
    def __init__(self, response: MockHTTPResponse):
        self._response = response
        self.requests: list = []

    def request(self, method: str, path: str, body: Optional[str] = None, headers: Optional[dict] = None):
        self.requests.append({"method": method, "path": path, "body": body, "headers": headers})

    def getresponse(self):
        return self._response

    def close(self):
        pass


@contextmanager
def mock_api(status: int = 200, body: Optional[Union[dict, str]] = None):
    """Patch the HTTP connection to return the given response; yields the connection for assertions."""
    conn = MockHTTPConnection(MockHTTPResponse(status, body))
    with patch("ddtrace.llmobs._prompts.manager.get_connection", lambda *a, **k: conn):
        yield conn


def assert_prompt_matches_response(prompt, response, expected_source):
    """Assert prompt fields match the API response."""
    assert prompt.id == response["prompt_id"]
    assert prompt.version == response["version"]
    assert prompt.labels == response.get("labels", [])
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
            with pytest.warns(DDTraceDeprecationWarning):
                prod_prompt = LLMObs.get_prompt("greeting", label="production")
        assert prod_prompt.version == "v1"
        assert prod_prompt.labels == ["development", "production"]

        LLMObs.clear_prompt_cache(hot=True, warm=True)

        with mock_api(200, DEV_PROMPT_RESPONSE):
            with pytest.warns(DDTraceDeprecationWarning):
                dev_prompt = LLMObs.get_prompt("greeting", label="development")
        assert dev_prompt.version == "dev-v1"
        assert dev_prompt.labels == ["development"]
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
                with pytest.warns(DDTraceDeprecationWarning):
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
                assert "label" not in prompt_data
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

    def test_auto_prompt_tracking_tags_llm_span_from_format(self, tracer):
        """A prompt rendered via format() and passed straight into an auto-instrumented LLM call
        gets tagged automatically, with no annotation_context needed.
        """
        LLMObs.enable(_tracer=tracer, agentless_enabled=False)
        integration = BaseLLMIntegration(IntegrationConfig(config, "fake_llm"))

        with mock_api(200, TEXT_PROMPT_RESPONSE):
            prompt = LLMObs.get_prompt("greeting")
        rendered = prompt.format(name="Alice")

        with LLMObs.llm(model_name="test-model", name="test") as span:
            integration.llmobs_set_tags(span, args=[], kwargs={"messages": rendered})

        prompt_data = get_llmobs_input_prompt(span)
        assert prompt_data["id"] == "greeting"
        assert prompt_data["variables"] == {"name": "Alice"}

    def test_auto_prompt_tracking_yields_to_explicit_annotation(self, tracer):
        """An explicit annotation_context(prompt=...) still wins over an auto-tracked prompt."""
        LLMObs.enable(_tracer=tracer, agentless_enabled=False)
        integration = BaseLLMIntegration(IntegrationConfig(config, "fake_llm"))

        with mock_api(200, TEXT_PROMPT_RESPONSE):
            auto_prompt = LLMObs.get_prompt("greeting")
        with mock_api(200, CHAT_PROMPT_RESPONSE):
            explicit_prompt = LLMObs.get_prompt("assistant")
        rendered = auto_prompt.format(name="Alice")

        with LLMObs.annotation_context(prompt=explicit_prompt.to_annotation_dict(persona="pirate", question="hi")):
            with LLMObs.llm(model_name="test-model", name="test") as span:
                integration.llmobs_set_tags(span, args=[], kwargs={"messages": rendered})

        prompt_data = get_llmobs_input_prompt(span)
        assert prompt_data["id"] == "assistant"

    def test_auto_prompt_tracking_matches_correct_prompt_among_concurrent_renders(self, tracer):
        """Two prompts rendered in the same trace attach to their own LLM span each, even when
        the calls consuming them complete out of render order.
        """
        LLMObs.enable(_tracer=tracer, agentless_enabled=False)
        integration = BaseLLMIntegration(IntegrationConfig(config, "fake_llm"))

        with mock_api(200, TEXT_PROMPT_RESPONSE):
            prompt_a = LLMObs.get_prompt("greeting")
        with mock_api(200, CHAT_PROMPT_RESPONSE):
            prompt_b = LLMObs.get_prompt("assistant")

        with LLMObs.workflow(name="handle-both"):
            rendered_a = prompt_a.format(name="Alice")
            rendered_b = prompt_b.format(persona="pirate", question="ahoy?")

            # call for prompt_b finishes first, even though prompt_a rendered first
            with LLMObs.llm(model_name="test-model", name="call-b") as span_b:
                integration.llmobs_set_tags(span_b, args=[], kwargs={"messages": rendered_b})
            with LLMObs.llm(model_name="test-model", name="call-a") as span_a:
                integration.llmobs_set_tags(span_a, args=[], kwargs={"messages": rendered_a})

        assert get_llmobs_input_prompt(span_a)["id"] == "greeting"
        assert get_llmobs_input_prompt(span_b)["id"] == "assistant"

    def test_auto_prompt_tracking_skips_untracked_input(self, tracer):
        """A request that carries no managed-prompt render (e.g. the render was copied or rebuilt)
        leaves the span untagged rather than guessing.
        """
        LLMObs.enable(_tracer=tracer, agentless_enabled=False)
        integration = BaseLLMIntegration(IntegrationConfig(config, "fake_llm"))

        with mock_api(200, TEXT_PROMPT_RESPONSE):
            prompt = LLMObs.get_prompt("greeting")
        rendered = prompt.format(name="Alice")

        with LLMObs.llm(model_name="test-model", name="test") as span:
            # str() drops the tracked-prompt subclass, mirroring a caller that copies the render
            integration.llmobs_set_tags(span, args=[], kwargs={"messages": str(rendered)})

        assert get_llmobs_input_prompt(span) is None

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

        spec = _PromptRequest(prompt_id="greeting", label="production")
        with patch.object(manager, "_background_refresh", return_value=None) as refresh_mock:
            with patch("ddtrace.llmobs._prompts.manager.threading.Thread", ImmediateThread):
                manager._trigger_background_refresh(spec)
                assert spec.key not in manager._refresh_threads
                manager._trigger_background_refresh(spec)

        assert refresh_mock.call_count == 2

    # --- get_prompt routing demux ---

    def test_route_no_env_to_http(self):
        manager = _make_manager(agentless=False)
        with mock_api(200, TEXT_PROMPT_RESPONSE):
            with patch.object(manager, "_fetch_from_ff") as ff_mock:
                with patch("ddtrace.llmobs._prompts.manager.config") as cfg:
                    cfg.env = None
                    prompt = manager.get_prompt("greeting")
        ff_mock.assert_not_called()
        assert prompt.source == "registry"

    def test_route_env_agent_to_ff(self):
        manager = _make_manager(agentless=False)
        with _ffe_enabled():
            _deliver_prompt_flag(
                "greeting",
                {
                    "prompt_id": "greeting",
                    "version": "ff-v1",
                    "labels": ["development", "production"],
                    "template": "FF Hello!",
                },
            )
            with patch.object(manager, "_get_prompt_http") as http_mock:
                prompt = manager.get_prompt("greeting")
        http_mock.assert_not_called()
        assert prompt.source == "ff"
        assert prompt.version == "ff-v1"
        assert prompt.labels == ["development", "production"]
        assert prompt.template == "FF Hello!"

    def test_route_env_agentless_to_http_resolve(self):
        manager = _make_manager(agentless=True)
        sentinel = ManagedPrompt(id="greeting", version="v1", labels=["production"], source="resolve", template="Hi")
        with patch.object(manager, "_fetch_from_ff") as ff_mock:
            with patch.object(manager, "_get_prompt_http", return_value=sentinel) as http_mock:
                with patch("ddtrace.llmobs._prompts.manager.config") as cfg:
                    cfg.env = "production"
                    prompt = manager.get_prompt("greeting", targeting_key="user-1")
        ff_mock.assert_not_called()
        (spec,), kwargs = http_mock.call_args
        assert spec.use_resolve and spec.env == "production" and spec.targeting_key == "user-1"
        assert kwargs["fallback"] is None
        assert prompt.source == "resolve"

    def test_route_targeting_key_to_ff(self):
        manager = _make_manager(agentless=False)
        ff_prompt = ManagedPrompt(
            id="greeting",
            version="ff-v1",
            labels=[],
            source="ff",
            template="Hello!",
        )
        with patch.object(manager, "_fetch_from_ff", return_value=ff_prompt) as ff_mock:
            with patch("ddtrace.llmobs._prompts.manager.config") as cfg:
                cfg.env = "staging"
                manager.get_prompt("greeting", targeting_key="user-123", tier="premium")
        ff_mock.assert_called_once_with("greeting", "user-123", {"tier": "premium"})

    def test_route_label_targeting_conflict_warns(self):
        manager = _make_manager()
        with mock_api(200, TEXT_PROMPT_RESPONSE):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                manager.get_prompt("greeting", label="production", targeting_key="user-123")
        conflict = [x for x in w if x.category is UserWarning and "targeting" in str(x.message).lower()]
        assert conflict, [str(x.message) for x in w]
        assert "label" in str(conflict[0].message)

    # NOT_READY (and NO_FLAG) are not hard failures: both fall through to the HTTP /resolve floor,
    # which resolves the same env-scoped variant server-side.
    def test_route_not_ready_to_http_resolve(self):
        manager = _make_manager()
        sentinel = ManagedPrompt(id="greeting", version="v1", labels=["staging"], source="resolve", template="Hi")
        with _ffe_enabled():
            with patch.object(manager, "_get_prompt_http", return_value=sentinel) as http_mock:
                prompt = manager.get_prompt("greeting")
        (spec,), kwargs = http_mock.call_args
        assert spec.use_resolve and spec.env == "staging"
        assert kwargs["fallback"] is None
        assert prompt.source == "resolve"

    def test_route_no_flag_to_http_resolve(self):
        manager = _make_manager()
        sentinel = ManagedPrompt(id="greeting", version="v1", labels=["staging"], source="resolve", template="Hi")
        with _ffe_enabled():
            _deliver_prompt_flag("other-prompt", {"prompt_id": "other-prompt", "version": "1", "template": "x"})
            with patch.object(manager, "_get_prompt_http", return_value=sentinel) as http_mock:
                prompt = manager.get_prompt("greeting")
        (spec,), kwargs = http_mock.call_args
        assert spec.use_resolve and spec.env == "staging"
        assert kwargs["fallback"] is None
        assert prompt.source == "resolve"

    # --- HTTP /resolve request shape ---

    def test_resolve_sends_jsonapi_body(self):
        manager = _make_manager(agentless=True)
        with mock_api(200, TEXT_PROMPT_RESPONSE) as conn:
            with patch("ddtrace.llmobs._prompts.manager.config") as cfg:
                cfg.env = "production"
                prompt = manager.get_prompt("greeting", targeting_key="user-1", tier="gold")
        req = conn.requests[-1]
        assert req["method"] == "POST"
        assert req["path"].endswith("/greeting/resolve")
        assert req["headers"]["Content-Type"] == "application/json"
        assert req["headers"]["DD-APPLICATION-KEY"] == "test-app-key"
        payload = json.loads(req["body"])
        assert payload["data"]["type"] == "prompt_resolve_requests"
        assert payload["data"]["attributes"] == {
            "env": "production",
            "targeting_key": "user-1",
            "context": {"tier": "gold"},
        }
        assert prompt.source == "resolve"

    def test_resolve_omits_targeting_and_context_when_absent(self):
        manager = _make_manager(agentless=True)
        with mock_api(200, TEXT_PROMPT_RESPONSE) as conn:
            with patch("ddtrace.llmobs._prompts.manager.config") as cfg:
                cfg.env = "production"
                manager.get_prompt("greeting")
        payload = json.loads(conn.requests[-1]["body"])
        assert payload["data"]["attributes"] == {"env": "production"}

    def test_label_still_uses_static_registry_get(self):
        manager = _make_manager(agentless=True)
        with mock_api(200, TEXT_PROMPT_RESPONSE) as conn:
            manager.get_prompt("greeting", label="production")
        req = conn.requests[-1]
        assert req["method"] == "GET"
        assert req["path"].endswith("/greeting?label=production")

    def test_resolve_caches_per_attributes(self):
        """Same context is cached; differing attributes are a distinct key (never wrong variant)."""
        manager = _make_manager(agentless=True)
        conns = []

        def conn_factory(*a, **k):
            conn = MockHTTPConnection(MockHTTPResponse(200, TEXT_PROMPT_RESPONSE))
            conns.append(conn)
            return conn

        with patch("ddtrace.llmobs._prompts.manager.get_connection", conn_factory):
            with patch("ddtrace.llmobs._prompts.manager.config") as cfg:
                cfg.env = "production"
                manager.get_prompt("greeting", targeting_key="u1", tier="gold")
                manager.get_prompt("greeting", targeting_key="u1", tier="gold")  # cache hit
                manager.get_prompt("greeting", targeting_key="u1", tier="free")  # new attrs -> new fetch
        assert len(conns) == 2

    def test_no_app_key_env_uses_fallback_without_calling_resolve(self):
        """Without an app key/SAT, /resolve can't be authorized; use the fallback and skip the doomed call."""
        manager = PromptManager(api_key="test-key", base_url="https://api.datadoghq.com", file_cache_enabled=False)
        with mock_api(200, TEXT_PROMPT_RESPONSE) as conn:
            with patch("ddtrace.llmobs._prompts.manager.config") as cfg:
                cfg.env = "production"
                prompt = manager.get_prompt("greeting", targeting_key="user-1", fallback="hi {name}")
        assert prompt.source == "fallback"
        assert conn.requests == []

    def test_resolve_uses_hot_cache_not_warm(self, tmp_path):
        """Resolve results stay in the bounded hot cache; only the static/label path persists to disk."""
        manager = PromptManager(
            api_key="test-key",
            app_key="test-app-key",
            base_url="https://api.datadoghq.com",
            file_cache_enabled=True,
            cache_dir=str(tmp_path),
        )
        with mock_api(200, TEXT_PROMPT_RESPONSE):
            with patch("ddtrace.llmobs._prompts.manager.config") as cfg:
                cfg.env = "production"
                prompt = manager.get_prompt("greeting", targeting_key="u1")
        assert prompt.source == "resolve"
        assert list(tmp_path.rglob("*.json")) == []

        with mock_api(200, TEXT_PROMPT_RESPONSE):
            manager.get_prompt("greeting", label="production")
        assert list(tmp_path.rglob("*.json"))


def _make_manager(app_key="test-app-key", agentless=False):
    return PromptManager(
        api_key="test-key",
        app_key=app_key,
        base_url="https://api.datadoghq.com",
        file_cache_enabled=False,
        agentless=agentless,
    )


@contextmanager
def _ffe_enabled():
    """Enable the FFE opt-in and set DD_ENV - the routing preconditions, not the eval."""
    import ddtrace.internal.settings.openfeature as ffe_settings

    with patch.object(ffe_settings.config, "experimental_flagging_provider_enabled", True):
        with patch("ddtrace.llmobs._prompts.manager.config") as cfg:
            cfg.env = "staging"
            yield


def _deliver_prompt_flag(prompt_id, variant_value):
    """Deliver a managed prompt flag via the real Remote Config ingestion path."""
    from ddtrace.internal.openfeature._native import process_ffe_configuration
    from tests.openfeature.config_helpers import create_config
    from tests.openfeature.config_helpers import create_json_flag

    process_ffe_configuration(create_config(create_json_flag(f"__llmobs__.prompt.{prompt_id}", variant_value)))


@pytest.fixture(autouse=True)
def _reset_ffe_global_config():
    """Clear the global FFE config around every test so deliveries don't leak across tests."""
    from ddtrace.internal.openfeature._config import _set_ffe_config

    _set_ffe_config(None)
    yield
    _set_ffe_config(None)


def _mock_write_api(status=200, body=None):
    """Create a mock API that captures request details."""
    conn = MockHTTPConnection(MockHTTPResponse(status, body))
    return conn, patch("ddtrace.llmobs._prompts.manager.get_connection", lambda *a, **k: conn)


class TestPromptManagement:
    """Tests for prompt management (write) operations."""

    @pytest.mark.parametrize(
        "status,exc_type",
        [
            (400, PromptValidationError),
            (401, PromptAuthError),
            (403, PromptAuthError),
            (404, PromptNotFoundError),
            (409, PromptConflictError),
            (500, PromptServerError),
            (503, PromptServerError),
        ],
    )
    def test_request_error_status_codes(self, status, exc_type):
        manager = _make_manager()
        conn, mock_patch = _mock_write_api(status, {"detail": "something went wrong"})
        with mock_patch:
            with pytest.raises(exc_type) as exc_info:
                manager.list_prompts()
            assert exc_info.value.status == status

    def test_delete_prompt_evicts_cache(self):
        manager = _make_manager()
        manager._hot_cache.set(
            "my-prompt:production",
            ManagedPrompt(id="my-prompt", version="v1", labels=["production"], source="registry", template=[]),
        )
        assert len(manager._hot_cache) == 1

        conn, mock_patch = _mock_write_api(200, {})
        with mock_patch:
            manager.delete_prompt("my-prompt")

        assert len(manager._hot_cache) == 0

    def test_enable_with_app_key_refreshes_manager_cached_by_read_path(self, tracer):
        """Regression: a read path that builds the prompt manager before enable(app_key=...)
        must not strand write APIs with the then-empty app key.
        """
        LLMObs._app_key = ""
        with mock_api(200, TEXT_PROMPT_RESPONSE):
            LLMObs.get_prompt("greeting")
        assert LLMObs._prompt_manager is not None
        assert LLMObs._prompt_manager._app_key == ""

        LLMObs.enable(_tracer=tracer, app_key="new-app-key", agentless_enabled=False)

        assert LLMObs._prompt_manager is None
        assert LLMObs._ensure_prompt_manager()._app_key == "new-app-key"

    def test_refresh_prompt_requires_api_key(self):
        manager = _make_manager()
        manager._headers["DD-API-KEY"] = ""
        with pytest.raises(PromptAuthError):
            manager.refresh_prompt("greeting")

    def test_crud_requires_api_key_raises_prompt_auth_error(self):
        with override_global_config(dict(_dd_api_key="")):
            LLMObs._prompt_manager = None
            with pytest.raises(PromptAuthError):
                LLMObs.create_prompt("greeting", [{"role": "user", "content": "hi"}])

    def test_request_transport_error_raises_prompt_api_error(self):
        manager = _make_manager()
        with patch.object(manager, "_http_request", side_effect=RuntimeError("connection failed")):
            with pytest.raises(PromptAPIError) as exc_info:
                manager.list_prompts()
        assert exc_info.value.status == 0
        assert "connection failed" in exc_info.value.detail

    def test_request_status_error_records_crud_telemetry(self):
        manager = _make_manager()
        conn, mock_patch = _mock_write_api(500, {"detail": "boom"})
        with mock_patch, patch("ddtrace.llmobs._prompts.manager.telemetry.record_prompt_crud_error") as record:
            with pytest.raises(PromptServerError):
                manager.list_prompts()
        record.assert_called_once_with("GET", "PromptServerError", 500)

    def test_request_transport_error_records_crud_telemetry(self):
        manager = _make_manager()
        with (
            patch.object(manager, "_http_request", side_effect=RuntimeError("connection failed")),
            patch("ddtrace.llmobs._prompts.manager.telemetry.record_prompt_crud_error") as record,
        ):
            with pytest.raises(PromptAPIError):
                manager.list_prompts()
        record.assert_called_once_with("GET", "PromptAPIError", 0)

    def test_request_missing_api_key_records_crud_telemetry(self):
        manager = _make_manager()
        manager._headers["DD-API-KEY"] = ""
        with patch("ddtrace.llmobs._prompts.manager.telemetry.record_prompt_crud_error") as record:
            with pytest.raises(PromptAuthError):
                manager.list_prompts()
        record.assert_called_once_with("GET", "PromptAuthError", 0)

    @pytest.mark.parametrize(
        "response,call,expected",
        [
            (
                {"ID": "prompt-uuid", "prompt_id": "p1"},
                lambda m: m.delete_prompt("p1"),
                {"id": "prompt-uuid", "prompt_id": "p1"},
            ),
            (
                [{"ID": "prompt-uuid", "prompt_id": "p1"}],
                lambda m: m.list_prompts(),
                [{"id": "prompt-uuid", "prompt_id": "p1"}],
            ),
        ],
    )
    def test_request_normalizes_backend_id_key(self, response, call, expected):
        manager = _make_manager()
        _, mock_patch = _mock_write_api(200, response)
        with mock_patch:
            result = call(manager)
        assert result == expected

    def test_hot_evict_does_not_over_evict_colon_prefixed_ids(self):
        """evict_prompt('foo') must not drop a different prompt 'foo:bar' that shares the key prefix."""
        manager = _make_manager()
        manager._hot_cache.set(
            "foo:production",
            ManagedPrompt(id="foo", version="v1", labels=["production"], source="registry", template=[]),
        )
        manager._hot_cache.set(
            "foo:bar:production",
            ManagedPrompt(id="foo:bar", version="v1", labels=["production"], source="registry", template=[]),
        )
        assert len(manager._hot_cache) == 2

        manager._evict_prompt_caches("foo")

        assert manager._hot_cache.get("foo:production") is None
        assert manager._hot_cache.get("foo:bar:production") is not None

    def test_warm_cache_distinct_ids_do_not_collide_on_path(self, tmp_path):
        """Regression: 'a/b' and 'a_b' must not share a cache file (lossy sanitization served wrong prompts)."""
        cache = WarmCache(cache_dir=str(tmp_path), ttl_seconds=60)
        cache.set("a/b:", ManagedPrompt(id="a/b", version="v1", labels=[], source="registry", template=[]))
        cache.set("a_b:", ManagedPrompt(id="a_b", version="v2", labels=[], source="registry", template=[]))

        assert cache.get("a/b:")[0].id == "a/b"
        assert cache.get("a_b:")[0].id == "a_b"

    @pytest.mark.parametrize("call", [lambda m: m.update_prompt("p1"), lambda m: m.update_prompt_version("p1", 1)])
    def test_update_requires_a_field(self, call):
        with pytest.raises(PromptValidationError) as exc_info:
            call(_make_manager())
        assert exc_info.value.status == 0

    @pytest.mark.parametrize(
        "body,expected",
        [("", {}), ("not json{", PromptServerError)],
    )
    def test_request_2xx_body_handling(self, body, expected):
        manager = _make_manager()
        conn, mock_patch = _mock_write_api(200, body)
        with mock_patch:
            if isinstance(expected, type) and issubclass(expected, Exception):
                with pytest.raises(expected):
                    manager.list_prompts()
            else:
                assert manager.delete_prompt("p1") == expected


def test_hot_cache_lru_eviction():
    from ddtrace.llmobs._prompts.cache import HotCache

    cache = HotCache(ttl_seconds=60, maxsize=2)

    def mk(v):
        return ManagedPrompt(id=v, version="1", labels=[], source="resolve", template="x")

    cache.set("a", mk("a"))
    cache.set("b", mk("b"))
    cache.get("a")  # touch 'a' so 'b' is now least-recently-used
    cache.set("c", mk("c"))  # over maxsize -> evict 'b'
    assert cache.get("b") is None
    assert cache.get("a") is not None
    assert cache.get("c") is not None
