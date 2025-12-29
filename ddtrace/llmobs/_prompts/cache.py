from collections import OrderedDict
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from time import time
from typing import TYPE_CHECKING
from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple
from typing import Union

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import DEFAULT_PROMPTS_CACHE_MAX_SIZE
from ddtrace.llmobs._constants import DEFAULT_PROMPTS_CACHE_TTL


if TYPE_CHECKING:
    from ddtrace.llmobs._prompts.prompt import ManagedPrompt

log = get_logger(__name__)


class CacheEntry:
    __slots__ = ("prompt", "timestamp")

    def __init__(self, prompt: "ManagedPrompt", timestamp: float) -> None:
        self.prompt = prompt
        self.timestamp = timestamp

    def is_stale(self, ttl: float) -> bool:
        return (time() - self.timestamp) > ttl


class HotCache:
    """
    In-memory LRU cache with TTL for prompt templates.

    Thread-safe via RLock.
    """

    def __init__(
        self,
        max_size: int = DEFAULT_PROMPTS_CACHE_MAX_SIZE,
        ttl_seconds: float = DEFAULT_PROMPTS_CACHE_TTL,
    ) -> None:
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._lock = RLock()

    def get(self, key: str) -> Optional[Tuple["ManagedPrompt", bool]]:
        """
        Get a prompt from cache.

        Returns:
            Tuple of (prompt, is_stale) if found, None otherwise.
        """
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            self._cache.move_to_end(key)
            return (entry.prompt, entry.is_stale(self._ttl))

    def set(self, key: str, prompt: "ManagedPrompt") -> None:
        """Add or update a prompt in cache."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
            while len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
            self._cache[key] = CacheEntry(prompt=prompt, timestamp=time())

    def clear(self) -> None:
        """Clear all entries from cache."""
        with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)


class WarmCache:
    """
    File-based cache for prompt persistence across restarts.

    Can be disabled via environment variable or constructor.
    """

    @staticmethod
    def _get_default_cache_dir() -> Path:
        """Get the default cache directory, with fallback for environments without HOME."""
        try:
            return Path.home() / ".cache" / "datadog" / "llmobs" / "prompts"
        except RuntimeError:
            # Path.home() raises RuntimeError when HOME is unset and user ID
            # is not in passwd (common in containerized environments)
            return Path(tempfile.gettempdir()) / "datadog" / "llmobs" / "prompts"

    def __init__(
        self,
        cache_dir: Optional[Union[Path, str]] = None,
        enabled: bool = True,
    ) -> None:
        if cache_dir is None:
            self._cache_dir = self._get_default_cache_dir()
        elif isinstance(cache_dir, str):
            self._cache_dir = Path(cache_dir).expanduser()
        else:
            self._cache_dir = cache_dir
        self._enabled = enabled
        self._lock = RLock()

        if self._enabled:
            self._ensure_cache_dir()

    def _ensure_cache_dir(self) -> None:
        try:
            self._cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as e:
            log.warning("Failed to create prompt cache directory: %s", e)
            self._enabled = False

    def _key_to_path(self, key: str) -> Path:
        safe_key = key.replace(":", "_").replace("/", "_").replace("\\", "_")
        return self._cache_dir / f"{safe_key}.json"

    def get(self, key: str) -> Optional["ManagedPrompt"]:
        """Load a prompt from file cache."""
        if not self._enabled:
            return None

        path = self._key_to_path(key)
        try:
            with self._lock:
                if not path.exists():
                    return None
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return _deserialize_prompt(data)
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
            log.debug("Failed to read prompt from cache: %s", e)
            return None

    def set(self, key: str, prompt: "ManagedPrompt") -> None:
        """Save a prompt to file cache."""
        if not self._enabled:
            return

        path = self._key_to_path(key)
        try:
            data = _serialize_prompt(prompt)
            with self._lock:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                os.chmod(path, 0o600)
        except OSError as e:
            log.debug("Failed to write prompt to cache: %s", e)

    def clear(self) -> None:
        """Clear all cached prompts."""
        if not self._enabled:
            return

        with self._lock:
            try:
                for path in self._cache_dir.glob("*.json"):
                    path.unlink()
            except OSError as e:
                log.debug("Failed to clear prompt cache: %s", e)


def _serialize_prompt(prompt: "ManagedPrompt") -> Dict[str, Any]:
    """Serialize a ManagedPrompt to a JSON-compatible dict."""
    return {
        "id": prompt.id,
        "version": prompt.version,
        "label": prompt.label,
        "source": prompt.source,
        "template": prompt.template,
        "variables": prompt.variables,
    }


def _deserialize_prompt(data: Dict[str, Any]) -> "ManagedPrompt":
    """Deserialize a dict to a ManagedPrompt."""
    from ddtrace.llmobs._prompts.prompt import ManagedPrompt

    return ManagedPrompt(
        prompt_id=data["id"],
        version=data["version"],
        label=data["label"],
        source="cache",
        template=data["template"],
        variables=data.get("variables", []),
    )
