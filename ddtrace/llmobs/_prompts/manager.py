import atexit
import json
import threading
from typing import Dict
from typing import Optional
from typing import Tuple
from typing import Union
from urllib.parse import urlencode
from urllib.parse import urlparse

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs import _telemetry as telemetry
from ddtrace.llmobs._constants import DEFAULT_PROMPTS_CACHE_MAX_SIZE
from ddtrace.llmobs._constants import DEFAULT_PROMPTS_CACHE_TTL
from ddtrace.llmobs._constants import DEFAULT_PROMPTS_TIMEOUT
from ddtrace.llmobs._constants import PROMPTS_ENDPOINT
from ddtrace.llmobs._http import get_connection
from ddtrace.llmobs._prompts.cache import HotCache
from ddtrace.llmobs._prompts.cache import WarmCache
from ddtrace.llmobs._prompts.prompt import ManagedPrompt
from ddtrace.llmobs._prompts.utils import extract_template
from ddtrace.llmobs.types import PromptFallback


log = get_logger(__name__)


class PromptManager:
    """Manages prompt retrieval and caching."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        endpoint_override: Optional[str] = None,
        cache_ttl: float = DEFAULT_PROMPTS_CACHE_TTL,
        cache_max_size: int = DEFAULT_PROMPTS_CACHE_MAX_SIZE,
        timeout: float = DEFAULT_PROMPTS_TIMEOUT,
        file_cache_enabled: bool = True,
        cache_dir: Optional[str] = None,
    ) -> None:
        self._base_url = self._normalize_base_url(base_url, endpoint_override)
        self._timeout = timeout

        # Pre-build headers since they don't change
        self._headers: Dict[str, str] = {"DD-API-KEY": api_key, "Content-Type": "application/json"}

        self._hot_cache = HotCache(max_size=cache_max_size, ttl_seconds=cache_ttl)
        self._warm_cache = WarmCache(enabled=file_cache_enabled, cache_dir=cache_dir, ttl_seconds=cache_ttl)

        self._refresh_threads: Dict[str, threading.Thread] = {}
        self._refresh_lock = threading.Lock()
        atexit.register(self._wait_for_refreshes)

    def get_prompt(
        self,
        prompt_id: str,
        label: Optional[str] = None,
        fallback: PromptFallback = None,
    ) -> ManagedPrompt:
        """Retrieve a prompt template from the registry."""
        key = self._cache_key(prompt_id, label)

        # Try hot cache (in-memory)
        prompt = self._try_cache(self._hot_cache, key, prompt_id, label, "hot_cache")
        if prompt is not None:
            return prompt

        # Try warm cache (file-based)
        prompt = self._try_cache(self._warm_cache, key, prompt_id, label, "warm_cache", populate_hot=True)
        if prompt is not None:
            return prompt

        # Try sync fetch from registry
        fetched_prompt = self._fetch_and_cache(prompt_id, label, key, evict_on_not_found=False)
        if fetched_prompt is not None:
            telemetry.record_prompt_source("registry")
            return fetched_prompt

        # Fall back to user-provided or empty prompt
        telemetry.record_prompt_source("fallback")
        return self._create_fallback_prompt(prompt_id, fallback)

    def _try_cache(
        self,
        cache: Union[HotCache, WarmCache],
        key: str,
        prompt_id: str,
        label: Optional[str],
        source_name: str,
        populate_hot: bool = False,
    ) -> Optional[ManagedPrompt]:
        """Try to get prompt from cache, trigger refresh if stale."""
        result = cache.get(key)
        if result is None:
            return None
        prompt, is_stale = result
        if populate_hot:
            self._hot_cache.set(key, prompt)
        if is_stale:
            self._trigger_background_refresh(key, prompt_id, label)
        telemetry.record_prompt_source(source_name)
        return prompt

    def clear_cache(self, hot: bool = True, warm: bool = True) -> None:
        """Clear the prompt cache."""
        if hot:
            self._hot_cache.clear()
        if warm:
            self._warm_cache.clear()

    def refresh_prompt(self, prompt_id: str, label: Optional[str] = None) -> Optional[ManagedPrompt]:
        """Force refresh a prompt from the registry, or None if not found."""
        key = self._cache_key(prompt_id, label)
        return self._fetch_and_cache(prompt_id, label, key, evict_on_not_found=True)

    def _cache_key(self, prompt_id: str, label: Optional[str]) -> str:
        return f"{prompt_id}:{label or ''}"

    def _update_caches(self, key: str, prompt: ManagedPrompt) -> None:
        """Store a prompt in both hot and warm caches with source='cache'."""
        cached_prompt = prompt._with_source("cache")
        self._hot_cache.set(key, cached_prompt)
        self._warm_cache.set(key, cached_prompt)

    def _evict_caches(self, key: str) -> None:
        """Remove a prompt from both caches."""
        self._hot_cache.delete(key)
        self._warm_cache.delete(key)

    def _fetch_and_cache(
        self,
        prompt_id: str,
        label: Optional[str],
        key: str,
        evict_on_not_found: bool = False,
    ) -> Optional[ManagedPrompt]:
        """Fetch a prompt and update caches."""
        prompt, not_found = self._fetch_from_registry(prompt_id, label, timeout=self._timeout)

        if prompt is not None:
            self._update_caches(key, prompt)
            return prompt

        if not_found:
            telemetry.record_prompt_fetch_error("NotFound")
            if evict_on_not_found:
                self._evict_caches(key)
        else:
            telemetry.record_prompt_fetch_error("FetchError")

        return None

    def _trigger_background_refresh(self, key: str, prompt_id: str, label: Optional[str]) -> None:
        """Trigger a background refresh if not already in progress."""

        def run_refresh():
            try:
                self._background_refresh(key, prompt_id, label)
            finally:
                with self._refresh_lock:
                    self._refresh_threads.pop(key, None)

        with self._refresh_lock:
            if key in self._refresh_threads:
                return
            thread = threading.Thread(target=run_refresh, daemon=True)
            self._refresh_threads[key] = thread

        try:
            thread.start()
        except RuntimeError:
            with self._refresh_lock:
                self._refresh_threads.pop(key, None)
            log.debug("Failed to start background refresh thread for prompt %s", prompt_id)

    def _background_refresh(self, key: str, prompt_id: str, label: Optional[str]) -> None:
        """Refresh a prompt in the background."""
        self._fetch_and_cache(prompt_id, label, key, evict_on_not_found=True)

    def _wait_for_refreshes(self) -> None:
        """Wait for background refreshes to complete on exit."""
        with self._refresh_lock:
            threads = list(self._refresh_threads.values())
        for thread in threads:
            thread.join(timeout=self._timeout)

    def _fetch_from_registry(
        self, prompt_id: str, label: Optional[str], timeout: float
    ) -> Tuple[Optional[ManagedPrompt], bool]:
        """Fetch from registry. Returns (prompt, not_found)."""
        conn = None
        try:
            conn = get_connection(self._base_url, timeout=timeout)
            conn.request("GET", self._build_path(prompt_id, label), headers=self._headers)
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

    def _build_path(self, prompt_id: str, label: Optional[str]) -> str:
        """Build the request path for fetching a prompt."""
        endpoint = PROMPTS_ENDPOINT.lstrip("/")
        if label:
            query_params = urlencode({"label": label})
            return f"{endpoint}/{prompt_id}?{query_params}"
        return f"{endpoint}/{prompt_id}"

    @staticmethod
    def _normalize_base_url(base_url: str, endpoint_override: Optional[str]) -> str:
        """Normalize base URL for prompt fetches.

        - Default missing scheme to ``https://``.
        - For HTTP(S), normalize to a trailing ``/`` so relative endpoint joins
          always resolve from a directory base path.
        """
        url = endpoint_override or base_url
        if "://" not in url:
            url = "https://" + url

        parsed = urlparse(url)
        if parsed.scheme in ("http", "https") and not parsed.path.endswith("/"):
            parsed = parsed._replace(path=parsed.path + "/")
        return parsed.geturl()

    def _parse_response(self, body: str, prompt_id: str, label: Optional[str]) -> Optional[ManagedPrompt]:
        """Parse the API response into a ManagedPrompt."""
        try:
            data = json.loads(body)
            if not isinstance(data, dict):
                log.warning("Failed to parse prompt response: expected object, got %s", type(data).__name__)
                return None
            return ManagedPrompt(
                id=data.get("prompt_id", prompt_id),
                version=data.get("version", "unknown"),
                label=data.get("label", label),
                source="registry",
                template=extract_template(data, default=[]),
                _uuid=data.get("prompt_uuid"),
                _version_uuid=data.get("prompt_version_uuid"),
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            log.warning("Failed to parse prompt response: %s", e)
            return None

    def _create_fallback_prompt(
        self,
        prompt_id: str,
        fallback: PromptFallback = None,
    ) -> ManagedPrompt:
        """Create a fallback prompt when fetch fails."""
        fallback_type = "user-provided" if fallback else "empty"
        log.debug("Using %s fallback for prompt %s", fallback_type, prompt_id)
        return ManagedPrompt.from_fallback(prompt_id, fallback)
