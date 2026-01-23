import json
import threading
from typing import Dict
from typing import Optional
from typing import Set
from typing import Tuple
from urllib.parse import quote
from urllib.parse import urlencode

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs import _telemetry as telemetry
from ddtrace.llmobs._constants import DEFAULT_PROMPTS_CACHE_MAX_SIZE
from ddtrace.llmobs._constants import DEFAULT_PROMPTS_CACHE_TTL
from ddtrace.llmobs._constants import DEFAULT_PROMPTS_LABEL
from ddtrace.llmobs._constants import DEFAULT_PROMPTS_TIMEOUT
from ddtrace.llmobs._constants import PROMPTS_BASE_URL
from ddtrace.llmobs._constants import PROMPTS_ENDPOINT
from ddtrace.llmobs._http import get_connection
from ddtrace.llmobs._prompts.cache import HotCache
from ddtrace.llmobs._prompts.cache import WarmCache
from ddtrace.llmobs._prompts.prompt import ManagedPrompt
from ddtrace.llmobs._prompts.prompt import _extract_template
from ddtrace.llmobs.types import PromptFallback


log = get_logger(__name__)


class PromptManager:
    """
    Manages prompt retrieval with Stale-While-Revalidate caching.

    Three-layer cache:
    - Hot cache: HotCache (in-memory, ~100ns)
    - Warm cache: WarmCache (file-based, ~1ms)
    - Fallback: (user-provided or empty)
    """

    def __init__(
        self,
        api_key: str,
        ml_app: str,
        app_key: Optional[str] = None,
        endpoint_override: Optional[str] = None,
        hot_cache: Optional[HotCache] = None,
        warm_cache: Optional[WarmCache] = None,
        cache_ttl: float = DEFAULT_PROMPTS_CACHE_TTL,
        cache_max_size: int = DEFAULT_PROMPTS_CACHE_MAX_SIZE,
        timeout: float = DEFAULT_PROMPTS_TIMEOUT,
        file_cache_enabled: bool = True,
        cache_dir: Optional[str] = None,
    ) -> None:
        self._api_key = api_key
        self._app_key = app_key
        self._ml_app = ml_app
        self._endpoint_override = endpoint_override.rstrip("/") if endpoint_override else None
        self._timeout = timeout

        self._hot_cache = hot_cache or HotCache(
            max_size=cache_max_size,
            ttl_seconds=cache_ttl,
        )
        self._warm_cache = warm_cache or WarmCache(enabled=file_cache_enabled, cache_dir=cache_dir)

        self._refresh_in_progress: Set[str] = set()
        self._refresh_lock = threading.Lock()

    def get_prompt(
        self,
        prompt_id: str,
        label: Optional[str] = None,
        fallback: PromptFallback = None,
    ) -> ManagedPrompt:
        """
        Retrieve a prompt template from the registry.

        Uses Stale-While-Revalidate pattern:
        - Hot cache hit + fresh: return immediately
        - Hot cache hit + stale: return immediately, trigger background refresh
        - Warm cache hit: return, populate hot cache
        - All miss: sync fetch with timeout, then fallback
        """
        label = label or DEFAULT_PROMPTS_LABEL
        key = self._cache_key(prompt_id, label)

        # Try hot cache (in-memory)
        result = self._hot_cache.get(key)
        if result is not None:
            cached_prompt, is_stale = result
            if is_stale:
                self._trigger_background_refresh(key, prompt_id, label)
            telemetry.record_prompt_source("hot_cache", prompt_id)
            return cached_prompt

        # Try warm cache (file-based)
        warm_prompt = self._warm_cache.get(key)
        if warm_prompt is not None:
            self._hot_cache.set(key, warm_prompt)
            self._trigger_background_refresh(key, prompt_id, label)
            telemetry.record_prompt_source("warm_cache", prompt_id)
            return warm_prompt

        # Try sync fetch from registry
        fetched_prompt = self._fetch_and_cache(
            prompt_id, label, key, evict_on_not_found=False
        )
        if fetched_prompt is not None:
            telemetry.record_prompt_source("registry", prompt_id)
            return fetched_prompt

        # Fall back to user-provided or empty prompt
        telemetry.record_prompt_source("fallback", prompt_id)
        return self._create_fallback_prompt(prompt_id, label, fallback)

    def clear_cache(self, hot: bool = True, warm: bool = True) -> None:
        """Clear the prompt cache.

        Args:
            hot: If True, clear the hot (in-memory) cache.
            warm: If True, clear the warm (file-based) cache.
        """
        if hot:
            self._hot_cache.clear()
        if warm:
            self._warm_cache.clear()

    def refresh_prompt(self, prompt_id: str, label: Optional[str] = None) -> Optional[ManagedPrompt]:
        """Force refresh a specific prompt from the registry.

        Fetches the prompt synchronously and updates both caches.
        If the prompt no longer exists (404), evicts it from cache.

        Args:
            prompt_id: The prompt identifier.
            label: The prompt label. Defaults to DEFAULT_PROMPTS_LABEL.

        Returns:
            The refreshed prompt, or None if fetch failed or prompt not found.
        """
        label = label or DEFAULT_PROMPTS_LABEL
        key = self._cache_key(prompt_id, label)
        return self._fetch_and_cache(prompt_id, label, key, evict_on_not_found=True)

    def _cache_key(self, prompt_id: str, label: str) -> str:
        return f"{self._ml_app}:{prompt_id}:{label}"

    def _update_caches(self, key: str, prompt: ManagedPrompt) -> None:
        """Store a prompt in both L1 and L2 caches with source='cache'."""
        cached_prompt = prompt._with_source("cache")
        self._hot_cache.set(key, cached_prompt)
        self._warm_cache.set(key, cached_prompt)

    def _evict_caches(self, key: str) -> None:
        """Remove a prompt from both L1 and L2 caches."""
        self._hot_cache.delete(key)
        self._warm_cache.delete(key)

    def _fetch_and_cache(
        self,
        prompt_id: str,
        label: str,
        key: str,
        evict_on_not_found: bool = False,
    ) -> Optional[ManagedPrompt]:
        """Fetch a prompt and update caches.

        Args:
            prompt_id: The prompt identifier.
            label: The prompt label.
            key: The cache key.
            evict_on_not_found: If True, evict from caches when prompt is not found (404).

        Returns:
            The fetched prompt, or None if fetch failed.
        """
        prompt, not_found = self._fetch_from_registry(prompt_id, label, timeout=self._timeout)

        if prompt is not None:
            self._update_caches(key, prompt)
            return prompt

        if not_found:
            telemetry.record_prompt_fetch_error(prompt_id, "NotFound")
            if evict_on_not_found:
                self._evict_caches(key)
        else:
            telemetry.record_prompt_fetch_error(prompt_id, "FetchError")

        return None

    def _trigger_background_refresh(self, key: str, prompt_id: str, label: str) -> None:
        """Trigger a background refresh if not already in progress."""
        with self._refresh_lock:
            if key in self._refresh_in_progress:
                return
            self._refresh_in_progress.add(key)

        thread = threading.Thread(
            target=self._background_refresh,
            args=(key, prompt_id, label),
            daemon=True,
        )
        try:
            thread.start()
        except RuntimeError:
            # Thread creation can fail in resource-constrained environments.
            # Remove the key so future refresh attempts can retry.
            with self._refresh_lock:
                self._refresh_in_progress.discard(key)
            log.debug("Failed to start background refresh thread for prompt %s", prompt_id)

    def _background_refresh(self, key: str, prompt_id: str, label: str) -> None:
        """Refresh a prompt in the background."""
        self._fetch_and_cache(prompt_id, label, key, evict_on_not_found=True)
        with self._refresh_lock:
            self._refresh_in_progress.discard(key)

    def _fetch_from_registry(
        self, prompt_id: str, label: str, timeout: float
    ) -> Tuple[Optional[ManagedPrompt], bool]:
        """Fetch a prompt from the Datadog Prompt Registry.

        Returns:
            Tuple of (prompt, not_found) where:
            - (ManagedPrompt, False) on success
            - (None, True) if prompt doesn't exist (404)
            - (None, False) on other errors
        """
        conn = None
        try:
            conn = get_connection(self._endpoint_override or PROMPTS_BASE_URL, timeout=timeout)
            conn.request("GET", self._build_path(prompt_id, label), headers=self._build_headers())
            response = conn.getresponse()

            if response.status == 200:
                body = response.read().decode("utf-8")
                return self._parse_response(body, prompt_id, label), False
            return None, response.status == 404
        except Exception:
            return None, False
        finally:
            if conn is not None:
                conn.close()

    def _build_path(self, prompt_id: str, label: str) -> str:
        """Build the request path for fetching a prompt."""
        encoded_prompt_id = quote(prompt_id, safe="")
        query_params = urlencode({"label": label, "ml_app": self._ml_app})
        return f"{PROMPTS_ENDPOINT}/{encoded_prompt_id}?{query_params}"

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "DD-API-KEY": self._api_key,
            "Content-Type": "application/json",
        }
        if self._app_key:
            headers["dd-application-key"] = self._app_key
        return headers

    def _parse_response(self, body: str, prompt_id: str, label: str) -> Optional[ManagedPrompt]:
        """Parse the API response into a ManagedPrompt."""
        try:
            data = json.loads(body)
            return ManagedPrompt(
                id=data.get("prompt_id", prompt_id),
                version=data.get("version", "unknown"),
                label=data.get("label", label),
                source="registry",
                template=_extract_template(data, default=[]),
                variables=data.get("variables", []),
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            log.warning("Failed to parse prompt response: %s", e)
            return None

    def _create_fallback_prompt(
        self,
        prompt_id: str,
        label: str,
        fallback: PromptFallback = None,
    ) -> ManagedPrompt:
        """Create a fallback prompt when fetch fails."""
        fallback_type = "user-provided" if fallback else "empty"
        log.debug("Using %s fallback for prompt %s (label=%s)", fallback_type, prompt_id, label)
        return ManagedPrompt.from_fallback(prompt_id, label, fallback)
