"""Unit tests for the Managed Prompt Registry feature.

This file tests individual components in isolation:
- ManagedPrompt: rendering, immutability, conversion
- HotCache: in-memory caching with TTL and LRU
- WarmCache: file-based persistent caching
"""

from pathlib import Path
import tempfile
import time

import pytest

from ddtrace.llmobs._prompts.cache import HotCache
from ddtrace.llmobs._prompts.cache import WarmCache
from ddtrace.llmobs._prompts.prompt import ManagedPrompt
from ddtrace.llmobs._utils import _validate_prompt


class TestManagedPrompt:
    """Unit tests for ManagedPrompt class."""

    def test_text_template_rendering(self):
        """Test rendering a text template with variables."""
        prompt = ManagedPrompt(
            prompt_id="greeting",
            version="v1",
            label="prod",
            source="registry",
            template="Hello {{name}}!",
            variables=["name"],
        )

        assert prompt.format(name="Alice") == "Hello Alice!"

    def test_missing_variables_preserved(self):
        """Test that missing variables are left as placeholders (safe substitute)."""
        prompt = ManagedPrompt(
            prompt_id="greeting",
            version="v1",
            label="prod",
            source="registry",
            template="Hello {{name}}, welcome to {{company}}!",
            variables=["name", "company"],
        )

        # Partial variables: missing ones left as-is
        assert prompt.format(name="Alice") == "Hello Alice, welcome to {{company}}!"

        # No variables: all left as-is
        assert prompt.format() == "Hello {{name}}, welcome to {{company}}!"

    def test_chat_template_rendering(self):
        """Test rendering a chat template with variables."""
        prompt = ManagedPrompt(
            prompt_id="chat",
            version="v1",
            label="prod",
            source="registry",
            template=[
                {"role": "system", "content": "You are {{persona}}."},
                {"role": "user", "content": "{{question}}"},
            ],
            variables=["persona", "question"],
        )

        messages = prompt.format(persona="helpful", question="Hello?")
        assert isinstance(messages, list)
        assert len(messages) == 2
        assert messages[0]["content"] == "You are helpful."  # type: ignore[index]
        assert messages[1]["content"] == "Hello?"  # type: ignore[index]

    def test_to_messages_wraps_text_in_user_role(self):
        """Test that to_messages converts text templates to message format."""
        prompt = ManagedPrompt(
            prompt_id="text",
            version="v1",
            label="prod",
            source="registry",
            template="Hello {{name}}!",
        )

        messages = prompt.to_messages(name="Alice")
        assert messages == [{"role": "user", "content": "Hello Alice!"}]

    def test_to_anthropic_extracts_system(self):
        """Test that to_anthropic separates system message for Anthropic API format."""
        prompt = ManagedPrompt(
            prompt_id="chat",
            version="v1",
            label="prod",
            source="registry",
            template=[
                {"role": "system", "content": "Be helpful."},
                {"role": "user", "content": "Hi"},
            ],
        )

        system, messages = prompt.to_anthropic()
        assert system == "Be helpful."
        assert messages == [{"role": "user", "content": "Hi"}]

    def test_to_anthropic_concatenates_multiple_system_messages(self):
        """Test that multiple system messages are concatenated, not silently dropped."""
        prompt = ManagedPrompt(
            prompt_id="chat",
            version="v1",
            label="prod",
            source="registry",
            template=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "system", "content": "Always be concise."},
                {"role": "user", "content": "Hi"},
            ],
        )

        system, messages = prompt.to_anthropic()
        assert system == "You are a helpful assistant.\nAlways be concise."
        assert messages == [{"role": "user", "content": "Hi"}]

    def test_immutability(self):
        """Test that ManagedPrompt objects cannot be modified."""
        prompt = ManagedPrompt(
            prompt_id="test",
            version="v1",
            label="prod",
            source="registry",
            template="Hello!",
        )

        with pytest.raises(AttributeError, match="immutable"):
            prompt._id = "changed"  # type: ignore[misc]

    def test_to_annotation_dict(self):
        """Test conversion to annotation_context format."""
        prompt = ManagedPrompt(
            prompt_id="greeting",
            version="abc123",
            label="prod",
            source="registry",
            template="Hello {{user}}!",
        )

        result = prompt.to_annotation_dict()
        assert result["id"] == "greeting"
        assert result["version"] == "abc123"
        assert result["template"] == "Hello {{user}}!"
        assert result["variables"] == {}  # Empty when not provided
        assert result["tags"]["dd.prompt.source"] == "registry"
        assert result["tags"]["dd.prompt.label"] == "prod"

    def test_to_annotation_dict_variables_are_empty(self):
        """Test that to_annotation_dict() returns empty variables.

        Variables should be passed separately via prompt_variables= in annotation_context(),
        not through to_annotation_dict(). This keeps the Prompt object immutable.
        """
        prompt = ManagedPrompt(
            prompt_id="greeting",
            version="abc123",
            label="prod",
            source="registry",
            template="Hello {{user}}, welcome to {{place}}!",
        )

        result = prompt.to_annotation_dict()

        assert result["id"] == "greeting"
        assert result["variables"] == {}  # Empty - variables come from prompt_variables param


class TestHotCache:
    """Unit tests for in-memory cache."""

    def test_get_set(self):
        """Test basic cache operations."""
        cache = HotCache()
        prompt = ManagedPrompt(
            prompt_id="test",
            version="v1",
            label="prod",
            source="registry",
            template="Hello!",
        )

        cache.set("key", prompt)
        result = cache.get("key")
        assert result is not None
        retrieved, is_stale = result
        assert retrieved.id == "test"
        assert not is_stale

    def test_ttl_marks_stale(self):
        """Test that entries become stale after TTL."""
        cache = HotCache(ttl_seconds=0.1)
        prompt = ManagedPrompt(
            prompt_id="test",
            version="v1",
            label="prod",
            source="registry",
            template="Hello!",
        )

        cache.set("key", prompt)
        time.sleep(0.15)
        result = cache.get("key")
        assert result is not None
        _, is_stale = result
        assert is_stale

    def test_lru_eviction(self):
        """Test that LRU eviction works when max_size is reached."""
        cache = HotCache(max_size=2)

        for i in range(3):
            prompt = ManagedPrompt(
                prompt_id=f"prompt-{i}",
                version="v1",
                label="prod",
                source="registry",
                template=f"Template {i}",
            )
            cache.set(f"key-{i}", prompt)

        # First entry should be evicted
        assert cache.get("key-0") is None
        assert cache.get("key-1") is not None
        assert cache.get("key-2") is not None


class TestWarmCache:
    """Unit tests for file-based cache."""

    def test_persists_to_disk(self):
        """Test that WarmCache persists prompts to disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache1 = WarmCache(cache_dir=Path(tmpdir))
            prompt = ManagedPrompt(
                prompt_id="test",
                version="v1",
                label="prod",
                source="registry",
                template="Hello!",
            )
            cache1.set("key", prompt)

            # New cache instance reads from disk
            cache2 = WarmCache(cache_dir=Path(tmpdir))
            result = cache2.get("key")
            assert result is not None
            assert result.id == "test"
            assert result.source == "cache"  # Source changes to "cache" when loaded

    def test_accepts_string_path(self):
        """Test that WarmCache accepts string path (for env var config)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = WarmCache(cache_dir=tmpdir)  # String, not Path
            prompt = ManagedPrompt(
                prompt_id="test",
                version="v1",
                label="prod",
                source="registry",
                template="Hello!",
            )
            cache.set("key", prompt)
            assert cache.get("key") is not None

    def test_disabled_cache(self):
        """Test that disabled cache doesn't read/write."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = WarmCache(cache_dir=Path(tmpdir), enabled=False)
            prompt = ManagedPrompt(
                prompt_id="test",
                version="v1",
                label="prod",
                source="registry",
                template="Hello!",
            )
            cache.set("key", prompt)
            assert cache.get("key") is None


class TestValidatePromptIntegration:
    """Tests for integration with _validate_prompt (used by annotation_context)."""

    def test_managed_prompt_validates(self):
        """Test that ManagedPrompt works with _validate_prompt."""
        prompt = ManagedPrompt(
            prompt_id="greeting",
            version="abc123",
            label="prod",
            source="registry",
            template="Hello {{user}}!",
        )

        validated = _validate_prompt(prompt, strict_validation=False)

        assert validated["id"] == "greeting"
        assert validated["version"] == "abc123"
        assert validated["template"] == "Hello {{user}}!"
        tags = validated["tags"]
        assert isinstance(tags, dict)
        assert tags["dd.prompt.source"] == "registry"
        assert tags["dd.prompt.label"] == "prod"
