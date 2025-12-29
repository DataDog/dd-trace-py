"""End-to-end integration tests for the Managed Prompt feature.

This file tests realistic customer usage patterns:
1. Full prompt lifecycle with mocked API
2. Integration with annotation_context (actual LLMObs calls)
3. Caching behavior across "application restarts"
4. Fallback behavior on API failures
5. Telemetry recording
"""

import json
from pathlib import Path
import tempfile
import time
from typing import Dict
from unittest import mock
from unittest.mock import MagicMock
from unittest.mock import patch

from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import INPUT_PROMPT
from ddtrace.llmobs._prompts.cache import WarmCache
from ddtrace.llmobs._prompts.manager import PromptManager
from ddtrace.llmobs._prompts.prompt import ManagedPrompt
from tests.utils import DummyTracer
from tests.utils import override_global_config


class MockAPIServer:
    """Simulates the Datadog Prompt Registry API."""

    def __init__(self):
        self.prompts: Dict[str, Dict] = {}
        self.call_count = 0
        self.should_fail = False
        self.latency: float = 0.0

    def register_prompt(self, prompt_id: str, template: str, version: str = "v1"):
        self.prompts[prompt_id] = {
            "prompt_id": prompt_id,
            "version": version,
            "label": "prod",
            "template": template,
            "variables": [],
        }

    def update_prompt(self, prompt_id: str, template: str, version: str):
        if prompt_id in self.prompts:
            self.prompts[prompt_id]["template"] = template
            self.prompts[prompt_id]["version"] = version

    def get_connection(self, *args, **kwargs):
        return MockConnection(self)


class MockConnection:
    def __init__(self, server: MockAPIServer):
        self.server = server

    def request(self, method, url, headers=None):
        self.server.call_count += 1
        parts = url.split("/")
        prompt_id = parts[-1].split("?")[0] if parts else ""
        self._response_data = self.server.prompts.get(prompt_id)
        self._should_fail = self.server.should_fail

        if self.server.latency > 0:
            time.sleep(self.server.latency)

    def getresponse(self):
        response = MagicMock()
        if self._should_fail:
            response.status = 500
            response.read.return_value = b"Internal Server Error"
        elif self._response_data:
            response.status = 200
            response.read.return_value = json.dumps(self._response_data).encode("utf-8")
        else:
            response.status = 404
            response.read.return_value = b"Not Found"
        return response

    def close(self):
        """Close the connection (no-op for mock)."""
        pass


class TestPromptLifecycle:
    """
    Tests the full prompt lifecycle as a customer would experience it.

    Scenarios covered:
    1. Cold start: App starts, first call fetches from API
    2. Hot path: Subsequent calls return from cache instantly
    3. Stale refresh: After TTL, returns stale while refreshing in background
    4. Warm cache: After restart, uses file cache (no API call)
    5. Fallback: API failure uses user-provided fallback
    """

    def test_complete_lifecycle(self):
        """Test the full lifecycle of prompt retrieval."""
        with tempfile.TemporaryDirectory() as tmpdir:
            api = MockAPIServer()
            api.register_prompt("greeting", "Hello {{name}}, version 1!", version="v1")

            manager = PromptManager(
                api_key="test-key",
                site="datadoghq.com",
                ml_app="test-app",
                cache_ttl=0.1,
                warm_cache=WarmCache(cache_dir=Path(tmpdir)),
            )

            # === PHASE 1: Cold Start ===
            with patch("ddtrace.llmobs._prompts.manager.get_connection", api.get_connection):
                prompt = manager.get_prompt("greeting", label="prod")

            assert api.call_count == 1, "Cold start should fetch from API"
            assert prompt.source == "registry"
            assert prompt.format(name="Alice") == "Hello Alice, version 1!"

            # === PHASE 2: Hot Path (Fresh Cache) ===
            prompt2 = manager.get_prompt("greeting", label="prod")
            assert api.call_count == 1, "Cache hit should not call API"
            assert prompt2.source == "cache"

            # === PHASE 3: Stale + Background Refresh ===
            time.sleep(0.15)
            api.update_prompt("greeting", "Hello {{name}}, version 2!", version="v2")

            with patch("ddtrace.llmobs._prompts.manager.get_connection", api.get_connection):
                prompt3 = manager.get_prompt("greeting", label="prod")
                time.sleep(0.05)  # Allow background refresh

            assert prompt3.version == "v1", "Should return stale immediately"

            # Next call gets refreshed version
            prompt4 = manager.get_prompt("greeting", label="prod")
            assert prompt4.version == "v2", "Should have new version after refresh"

            # === PHASE 4: Simulate Restart (New Manager, Same Cache Dir) ===
            new_manager = PromptManager(
                api_key="test-key",
                site="datadoghq.com",
                ml_app="test-app",
                warm_cache=WarmCache(cache_dir=Path(tmpdir)),
            )

            calls_before = api.call_count
            prompt5 = new_manager.get_prompt("greeting", label="prod")
            assert api.call_count == calls_before, "Warm cache should avoid API call"
            assert prompt5.source == "cache"

            # === PHASE 5: API Failure + Fallback ===
            api.should_fail = True
            with patch("ddtrace.llmobs._prompts.manager.get_connection", api.get_connection):
                fallback_prompt = new_manager.get_prompt(
                    "unknown-prompt",
                    fallback="Default: {{name}}",
                )
            assert fallback_prompt.source == "fallback"
            assert fallback_prompt.format(name="Bob") == "Default: Bob"


class TestAnnotationContextIntegration:
    """
    Tests integration with actual LLMObs.annotation_context().

    These tests verify that ManagedPrompt works correctly when used with
    the real annotation_context API, ensuring future changes don't break integration.
    """

    def test_annotation_context_with_managed_prompt(self):
        """Test using ManagedPrompt with actual LLMObs.annotation_context."""
        with override_global_config(dict(_dd_api_key="<not-a-real-key>", _llmobs_ml_app="test-app")):
            dummy_tracer = DummyTracer()
            LLMObs.enable(_tracer=dummy_tracer, agentless_enabled=False)

            try:
                prompt = ManagedPrompt(
                    prompt_id="greeting",
                    version="abc123",
                    label="prod",
                    source="registry",
                    template="Hello {{user}}!",
                    variables=["user"],
                )

                # Use actual annotation_context
                with LLMObs.annotation_context(prompt=prompt):
                    with LLMObs.llm(model_name="test-model", name="test-llm") as span:
                        # Verify span has prompt metadata
                        prompt_data = span._get_ctx_item(INPUT_PROMPT)
                        assert prompt_data is not None
                        assert prompt_data["id"] == "greeting"
                        assert prompt_data["version"] == "abc123"
                        assert prompt_data["tags"]["dd.prompt.source"] == "registry"
                        assert prompt_data["tags"]["dd.prompt.label"] == "prod"
            finally:
                LLMObs.disable()

    def test_annotation_context_with_chat_prompt(self):
        """Test chat prompts work with annotation_context."""
        with override_global_config(dict(_dd_api_key="<not-a-real-key>", _llmobs_ml_app="test-app")):
            dummy_tracer = DummyTracer()
            LLMObs.enable(_tracer=dummy_tracer, agentless_enabled=False)

            try:
                prompt = ManagedPrompt(
                    prompt_id="chat-assistant",
                    version="def456",
                    label="prod",
                    source="registry",
                    template=[
                        {"role": "system", "content": "You are {{persona}}."},
                        {"role": "user", "content": "{{question}}"},
                    ],
                    variables=["persona", "question"],
                )

                with LLMObs.annotation_context(prompt=prompt):
                    with LLMObs.llm(model_name="test-model", name="test-llm") as span:
                        prompt_data = span._get_ctx_item(INPUT_PROMPT)
                        assert prompt_data is not None
                        assert prompt_data["id"] == "chat-assistant"
                        assert "chat_template" in prompt_data
            finally:
                LLMObs.disable()

    def test_annotation_context_with_prompt_variables(self):
        """Test that prompt_variables are captured in span metadata.

        This is the recommended pattern for managed prompts:
        - Pass the ManagedPrompt object as `prompt`
        - Pass the same variables used in format() as `prompt_variables`
        """
        with override_global_config(dict(_dd_api_key="<not-a-real-key>", _llmobs_ml_app="test-app")):
            dummy_tracer = DummyTracer()
            LLMObs.enable(_tracer=dummy_tracer, agentless_enabled=False)

            try:
                prompt = ManagedPrompt(
                    prompt_id="greeting",
                    version="abc123",
                    label="prod",
                    source="registry",
                    template="Hello {{user}}, welcome to {{city}}!",
                    variables=["user", "city"],
                )

                # The pattern: pass same variables to format() and prompt_variables
                template_vars = {"user": "Alice", "city": "Paris"}

                with LLMObs.annotation_context(prompt=prompt, prompt_variables=template_vars):
                    with LLMObs.llm(model_name="test-model", name="test-llm") as span:
                        prompt_data = span._get_ctx_item(INPUT_PROMPT)
                        assert prompt_data is not None
                        assert prompt_data["id"] == "greeting"
                        # Verify variables are captured
                        assert prompt_data["variables"] == {"user": "Alice", "city": "Paris"}
            finally:
                LLMObs.disable()

    def test_prompt_variables_override_existing(self):
        """Test that prompt_variables override any variables in the dict prompt."""
        with override_global_config(dict(_dd_api_key="<not-a-real-key>", _llmobs_ml_app="test-app")):
            dummy_tracer = DummyTracer()
            LLMObs.enable(_tracer=dummy_tracer, agentless_enabled=False)

            try:
                # Dict prompt with some variables already set
                prompt_dict = {
                    "id": "greeting",
                    "version": "1.0",
                    "template": "Hello {{user}}!",
                    "variables": {"user": "Bob", "extra": "value"},
                }

                # prompt_variables should override "user" but keep "extra"
                with LLMObs.annotation_context(prompt=prompt_dict, prompt_variables={"user": "Alice"}):
                    with LLMObs.llm(model_name="test-model", name="test-llm") as span:
                        prompt_data = span._get_ctx_item(INPUT_PROMPT)
                        assert prompt_data is not None
                        # "user" should be overridden, "extra" should be preserved
                        assert prompt_data["variables"]["user"] == "Alice"
                        assert prompt_data["variables"]["extra"] == "value"
            finally:
                LLMObs.disable()


class TestTelemetry:
    """Tests for telemetry recording."""

    def test_telemetry_records_source_on_registry_fetch(self):
        """Test that telemetry records source when fetching from registry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            api = MockAPIServer()
            api.register_prompt("greeting", "Hello!")

            manager = PromptManager(
                api_key="test-key",
                site="datadoghq.com",
                ml_app="test-app",
                warm_cache=WarmCache(cache_dir=Path(tmpdir)),
            )

            with patch("ddtrace.llmobs._prompts.manager.get_connection", api.get_connection):
                with mock.patch("ddtrace.llmobs._telemetry.telemetry_writer.add_count_metric") as mock_telemetry:
                    manager.get_prompt("greeting")

                    # Check that source was recorded
                    calls = mock_telemetry.call_args_list
                    source_calls = [c for c in calls if "prompt.source" in str(c)]
                    assert len(source_calls) == 1
                    # Verify it was from registry
                    call_kwargs = source_calls[0][1]
                    assert ("from", "registry") in call_kwargs["tags"]

    def test_telemetry_records_source_on_cache_hit(self):
        """Test that telemetry records source on cache hits."""
        with tempfile.TemporaryDirectory() as tmpdir:
            api = MockAPIServer()
            api.register_prompt("greeting", "Hello!")

            manager = PromptManager(
                api_key="test-key",
                site="datadoghq.com",
                ml_app="test-app",
                warm_cache=WarmCache(cache_dir=Path(tmpdir)),
            )

            # First call populates cache
            with patch("ddtrace.llmobs._prompts.manager.get_connection", api.get_connection):
                manager.get_prompt("greeting")

            # Second call should be from cache
            with mock.patch("ddtrace.llmobs._telemetry.telemetry_writer.add_count_metric") as mock_telemetry:
                manager.get_prompt("greeting")

                calls = mock_telemetry.call_args_list
                source_calls = [c for c in calls if "prompt.source" in str(c)]
                assert len(source_calls) == 1
                call_kwargs = source_calls[0][1]
                assert ("from", "l1_cache") in call_kwargs["tags"]

    def test_telemetry_records_fetch_error(self):
        """Test that telemetry records fetch errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            api = MockAPIServer()
            api.should_fail = True

            manager = PromptManager(
                api_key="test-key",
                site="datadoghq.com",
                ml_app="test-app",
                warm_cache=WarmCache(cache_dir=Path(tmpdir)),
                sync_timeout=0.1,
            )

            with patch("ddtrace.llmobs._prompts.manager.get_connection", api.get_connection):
                with mock.patch("ddtrace.llmobs._telemetry.telemetry_writer.add_count_metric") as mock_telemetry:
                    manager.get_prompt("greeting", fallback="Default")

                    calls = mock_telemetry.call_args_list
                    # Should have fallback source and possibly error
                    source_calls = [c for c in calls if "prompt.source" in str(c)]
                    fallback_source = [c for c in source_calls if ("from", "fallback") in c[1]["tags"]]
                    assert len(fallback_source) == 1


class TestLabelIsolation:
    """Tests for label-based prompt isolation."""

    def test_different_labels_cached_separately(self):
        """Test that prod and dev labels are cached independently."""
        with tempfile.TemporaryDirectory() as tmpdir:
            api = MockAPIServer()
            manager = PromptManager(
                api_key="test-key",
                site="datadoghq.com",
                ml_app="test-app",
                warm_cache=WarmCache(cache_dir=Path(tmpdir)),
            )

            # Register prod version
            api.prompts["greeting"] = {
                "prompt_id": "greeting",
                "version": "prod-v1",
                "label": "prod",
                "template": "PROD: Hello {{name}}!",
                "variables": ["name"],
            }

            with patch("ddtrace.llmobs._prompts.manager.get_connection", api.get_connection):
                prod = manager.get_prompt("greeting", label="prod")
            assert prod.format(name="Alice") == "PROD: Hello Alice!"

            # Update to dev version
            api.prompts["greeting"]["template"] = "DEV: Hello {{name}}!"
            api.prompts["greeting"]["version"] = "dev-v1"

            with patch("ddtrace.llmobs._prompts.manager.get_connection", api.get_connection):
                dev = manager.get_prompt("greeting", label="dev")
            assert dev.format(name="Bob") == "DEV: Hello Bob!"

            # Prod should still be cached separately
            prod_cached = manager.get_prompt("greeting", label="prod")
            assert prod_cached.format(name="Charlie") == "PROD: Hello Charlie!"
