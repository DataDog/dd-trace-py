import threading
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs import _telemetry as telemetry
from ddtrace.llmobs._constants import DEFAULT_PROMPTS_CACHE_MAX_SIZE
from ddtrace.llmobs._constants import DEFAULT_PROMPTS_CACHE_TTL
from ddtrace.llmobs._constants import DEFAULT_PROMPTS_FETCH_TIMEOUT
from ddtrace.llmobs._constants import DEFAULT_PROMPTS_LABEL
from ddtrace.llmobs._constants import DEFAULT_PROMPTS_SYNC_TIMEOUT
from ddtrace.llmobs._constants import PROMPTS_ENDPOINT
from ddtrace.llmobs._constants import PROMPTS_SUBDOMAIN
from ddtrace.llmobs._http import get_connection
from ddtrace.llmobs._prompts.cache import HotCache
from ddtrace.llmobs._prompts.cache import WarmCache
from ddtrace.llmobs._prompts.prompt import ManagedPrompt


log = get_logger(__name__)


class PromptManager:
    """
    Manages prompt retrieval with Stale-While-Revalidate caching.

    Three-layer cache:
    - L1: HotCache (in-memory, ~100ns)
    - L2: WarmCache (file-based, ~1ms)
    - L3: Fallback (user-provided or empty)
    """

    def __init__(
        self,
        api_key: str,
        site: str,
        ml_app: str,
        hot_cache: Optional[HotCache] = None,
        warm_cache: Optional[WarmCache] = None,
        cache_ttl: float = DEFAULT_PROMPTS_CACHE_TTL,
        cache_max_size: int = DEFAULT_PROMPTS_CACHE_MAX_SIZE,
        sync_timeout: float = DEFAULT_PROMPTS_SYNC_TIMEOUT,
        fetch_timeout: float = DEFAULT_PROMPTS_FETCH_TIMEOUT,
        file_cache_enabled: bool = True,
        cache_dir: Optional[str] = None,
    ) -> None:
        self._api_key = api_key
        self._site = site
        self._ml_app = ml_app
        self._sync_timeout = sync_timeout
        self._fetch_timeout = fetch_timeout

        self._hot_cache = hot_cache or HotCache(
            max_size=cache_max_size,
            ttl_seconds=cache_ttl,
        )
        self._warm_cache = warm_cache or WarmCache(enabled=file_cache_enabled, cache_dir=cache_dir)

        self._refresh_in_progress: set = set()
        self._refresh_lock = threading.Lock()

    def get_prompt(
        self,
        prompt_id: str,
        label: Optional[str] = None,
        fallback: Optional[Union[str, List[Dict[str, str]]]] = None,
    ) -> ManagedPrompt:
        """
        Retrieve a prompt template from the registry.

        Uses Stale-While-Revalidate pattern:
        - L1 hit + fresh: return immediately
        - L1 hit + stale: return immediately, trigger background refresh
        - L2 hit: return, populate L1
        - All miss: sync fetch with timeout, then fallback
        """
        label = label or DEFAULT_PROMPTS_LABEL
        key = self._cache_key(prompt_id, label)

        # Try L1 cache (hot cache)
        result = self._hot_cache.get(key)
        if result is not None:
            prompt, is_stale = result
            if is_stale:
                self._trigger_background_refresh(key, prompt_id, label)
            telemetry.record_prompt_source("l1_cache", prompt_id)
            return prompt

        # Try L2 cache (warm cache)
        prompt = self._warm_cache.get(key)
        if prompt is not None:
            self._hot_cache.set(key, prompt)
            self._trigger_background_refresh(key, prompt_id, label)
            telemetry.record_prompt_source("l2_cache", prompt_id)
            return prompt

        # Try sync fetch from registry
        prompt = self._sync_fetch(prompt_id, label, key)
        if prompt is not None:
            telemetry.record_prompt_source("registry", prompt_id)
            return prompt

        # Fall back to user-provided or empty prompt
        telemetry.record_prompt_source("fallback", prompt_id)
        return self._create_fallback_prompt(prompt_id, label, fallback)

    def _cache_key(self, prompt_id: str, label: str) -> str:
        return f"{self._ml_app}:{prompt_id}:{label}"

    def _sync_fetch(self, prompt_id: str, label: str, key: str) -> Optional[ManagedPrompt]:
        """Synchronous fetch with timeout for cold starts."""
        try:
            prompt = self._fetch_from_registry(prompt_id, label, timeout=self._sync_timeout)
            if prompt is not None:
                # Store cached version (source="cache") for future retrievals
                cached_prompt = prompt._with_source("cache")
                self._hot_cache.set(key, cached_prompt)
                self._warm_cache.set(key, cached_prompt)
                # Return original with source="registry" for this call
                return prompt
        except Exception as e:
            log.debug("Sync fetch failed for prompt %s: %s", prompt_id, e)
            telemetry.record_prompt_fetch_error(prompt_id, type(e).__name__)
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
        thread.start()

    def _background_refresh(self, key: str, prompt_id: str, label: str) -> None:
        """Refresh a prompt in the background."""
        try:
            prompt = self._fetch_from_registry(prompt_id, label, timeout=self._fetch_timeout)
            if prompt is not None:
                # Store cached version (source="cache") for future retrievals
                cached_prompt = prompt._with_source("cache")
                self._hot_cache.set(key, cached_prompt)
                self._warm_cache.set(key, cached_prompt)
        except Exception as e:
            log.debug("Background refresh failed for prompt %s: %s", prompt_id, e)
        finally:
            with self._refresh_lock:
                self._refresh_in_progress.discard(key)

    def _fetch_from_registry(self, prompt_id: str, label: str, timeout: float) -> Optional[ManagedPrompt]:
        """Fetch a prompt from the Datadog Prompt Registry."""
        conn = None
        try:
            intake_url = self._get_intake_url()
            path = self._build_path(prompt_id, label)
            headers = self._build_headers()

            conn = get_connection(intake_url, timeout=timeout)
            conn.request("GET", path, headers=headers)
            response = conn.getresponse()
            body = response.read().decode("utf-8")

            if response.status == 200:
                return self._parse_response(body, prompt_id, label)
            elif response.status == 304:
                # TODO: Future optimization - implement HTTP 304 Not Modified support.
                # Send local version hash in If-None-Match header, server returns 304 if unchanged.
                # This reduces payload size for unchanged prompts.
                return None
            elif response.status == 404:
                log.warning("Prompt not found: %s (label=%s)", prompt_id, label)
                return None
            else:
                log.warning("Failed to fetch prompt %s: status=%d", prompt_id, response.status)
                return None
        except Exception as e:
            log.debug("Error fetching prompt %s: %s", prompt_id, e)
            raise
        finally:
            if conn is not None:
                conn.close()

    def _get_intake_url(self) -> str:
        """Get the base intake URL for the Prompt Registry."""
        return f"https://{PROMPTS_SUBDOMAIN}.{self._site}"

    def _build_path(self, prompt_id: str, label: str) -> str:
        """Build the request path for fetching a prompt.

        TODO: Update path structure when the Prompt Registry API endpoint is created.
        Current placeholder follows Datadog API conventions.
        """
        return f"{PROMPTS_ENDPOINT}/{prompt_id}?label={label}&ml_app={self._ml_app}"

    def _build_headers(self) -> Dict[str, str]:
        return {
            "DD-API-KEY": self._api_key,
            "Content-Type": "application/json",
        }

    def _parse_response(self, body: str, prompt_id: str, label: str) -> Optional[ManagedPrompt]:
        """Parse the API response into a ManagedPrompt."""
        import json

        try:
            data = json.loads(body)
            template = data.get("template") or data.get("chat_template", [])
            template_type = "text" if isinstance(template, str) else "chat"

            return ManagedPrompt(
                prompt_id=data.get("prompt_id", prompt_id),
                version=data.get("version", "unknown"),
                label=data.get("label", label),
                source="registry",
                template=template,
                template_type=template_type,
                variables=data.get("variables", []),
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            log.warning("Failed to parse prompt response: %s", e)
            return None

    def _create_fallback_prompt(
        self,
        prompt_id: str,
        label: str,
        fallback: Optional[Union[str, List[Dict[str, str]]]] = None,
    ) -> ManagedPrompt:
        """Create a fallback prompt when fetch fails."""
        if fallback is not None:
            log.warning("Using user-provided fallback for prompt %s (label=%s)", prompt_id, label)
            template = fallback
            template_type = "text" if isinstance(fallback, str) else "chat"
        else:
            log.warning("Using empty fallback for prompt %s (label=%s)", prompt_id, label)
            template = ""
            template_type = "text"

        return ManagedPrompt(
            prompt_id=prompt_id,
            version="fallback",
            label=label,
            source="fallback",
            template=template,
            template_type=template_type,
            variables=[],
        )
