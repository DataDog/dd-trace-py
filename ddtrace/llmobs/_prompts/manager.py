import atexit
import json
import threading
from typing import Any
from typing import Literal
from typing import Optional
from typing import Union
from urllib.parse import quote
from urllib.parse import urlencode
import warnings

from ddtrace import config
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs import _telemetry as telemetry
from ddtrace.llmobs._constants import DEFAULT_PROMPTS_CACHE_TTL
from ddtrace.llmobs._constants import DEFAULT_PROMPTS_TIMEOUT
from ddtrace.llmobs._constants import PROMPTS_ENDPOINT
from ddtrace.llmobs._http import get_connection
from ddtrace.llmobs._prompts.cache import HotCache
from ddtrace.llmobs._prompts.cache import WarmCache
from ddtrace.llmobs._prompts.prompt import ManagedPrompt
from ddtrace.llmobs._prompts.utils import cache_key
from ddtrace.llmobs._prompts.utils import extract_error_detail
from ddtrace.llmobs._prompts.utils import extract_template
from ddtrace.llmobs.types import PromptFallback


log = get_logger(__name__)


class PromptManager:
    """Manages prompt retrieval and caching."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        cache_ttl: float = DEFAULT_PROMPTS_CACHE_TTL,
        timeout: float = DEFAULT_PROMPTS_TIMEOUT,
        file_cache_enabled: bool = True,
        cache_dir: Optional[str] = None,
        agentless: bool = True,
    ) -> None:
        self._base_url = base_url if "://" in base_url else "https://" + base_url
        self._timeout = timeout
        self._agentless = agentless
        self._headers: dict[str, str] = {
            "DD-API-KEY": api_key,
            "X-Datadog-SDK-Language": "python",
        }
        self._cache_enabled = cache_ttl > 0

        self._hot_cache = HotCache(ttl_seconds=cache_ttl)
        self._warm_cache = WarmCache(enabled=file_cache_enabled, cache_dir=cache_dir, ttl_seconds=cache_ttl)

        self._refresh_threads: dict[str, threading.Thread] = {}
        self._refresh_lock = threading.Lock()
        self._ffe_rc_enabled = False
        if file_cache_enabled:
            atexit.register(self._wait_for_refreshes)

    def get_prompt(
        self,
        prompt_id: str,
        *,
        label: Optional[str] = None,
        fallback: PromptFallback = None,
        targeting_key: Optional[str] = None,
        **attributes: Any,
    ) -> ManagedPrompt:
        """Retrieve a prompt template from the registry or FFE."""
        if label is not None and (targeting_key is not None or attributes):
            warnings.warn(
                "get_prompt() received 'label' alongside 'targeting_key' or other attributes. "
                "'label' routes to the HTTP path which does not support targeting; the extra "
                "arguments will be ignored. Drop 'label' to use Feature-Flag-Evaluation dispatch.",
                UserWarning,
                stacklevel=2,
            )

        dd_env = config.env
        if label is not None:
            telemetry.record_prompt_routing_signal("label_only")
        elif dd_env:
            telemetry.record_prompt_routing_signal("env_only")
        else:
            telemetry.record_prompt_routing_signal("neither")

        if label is None and dd_env and not self._agentless:
            prompt = self._fetch_from_ff(prompt_id, targeting_key, attributes)
            if prompt is not None:
                telemetry.record_prompt_source("ff")
                return prompt

        return self._get_prompt_http(prompt_id, label=label, fallback=fallback)

    def _get_prompt_http(
        self,
        prompt_id: str,
        label: Optional[str] = None,
        fallback: PromptFallback = None,
    ) -> ManagedPrompt:
        """Retrieve a prompt via the HTTP registry path."""
        key = cache_key(prompt_id, label)

        if self._cache_enabled:
            # Try hot cache (in-memory)
            prompt = self._try_cache(self._hot_cache, key, prompt_id, label, "hot_cache")
            if prompt is not None:
                return prompt

            # Try warm cache (file-based)
            prompt = self._try_cache(self._warm_cache, key, prompt_id, label, "warm_cache", populate_hot=True)
            if prompt is not None:
                return prompt

        # Try sync fetch from registry
        fetched_prompt, reason = self._fetch_and_cache(prompt_id, label, key, evict_on_not_found=False)
        if fetched_prompt is not None:
            telemetry.record_prompt_source("registry")
            return fetched_prompt

        # Fall back to user-provided or empty prompt
        telemetry.record_prompt_source("fallback")
        return self._create_fallback_prompt(prompt_id, fallback, reason=reason)

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
        key = cache_key(prompt_id, label)
        prompt, _ = self._fetch_and_cache(prompt_id, label, key, evict_on_not_found=True)
        return prompt

    def _update_caches(self, key: str, prompt: ManagedPrompt) -> None:
        """Store a prompt in both hot and warm caches with source='cache'."""
        if not self._cache_enabled:
            return
        cached_prompt = prompt._with_source("cache")
        self._hot_cache.set(key, cached_prompt)
        self._warm_cache.set(key, cached_prompt)

    def _evict_caches(self, key: str) -> None:
        """Remove a prompt from both caches."""
        if not self._cache_enabled:
            return
        self._hot_cache.delete(key)
        self._warm_cache.delete(key)

    def _fetch_and_cache(
        self,
        prompt_id: str,
        label: Optional[str],
        key: str,
        evict_on_not_found: bool = False,
    ) -> tuple[Optional[ManagedPrompt], str]:
        """Fetch a prompt and update caches."""
        prompt, not_found, reason = self._fetch_from_registry(prompt_id, label, timeout=self._timeout)

        if prompt is not None:
            self._update_caches(key, prompt)
            return prompt, ""

        if not_found:
            telemetry.record_prompt_fetch_error("NotFound")
            if evict_on_not_found:
                self._evict_caches(key)
        else:
            telemetry.record_prompt_fetch_error("FetchError")

        return None, reason

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

    def _ensure_ffe_rc(self) -> bool:
        """Lazily enable FFE Remote Config so flag configurations are delivered.

        Returns True if RC is enabled (either already or just now), False if enable failed.
        """
        if self._ffe_rc_enabled:
            return True
        try:
            from ddtrace.internal.openfeature._remoteconfiguration import enable_featureflags_rc

            enable_featureflags_rc()
            self._ffe_rc_enabled = True
            return True
        except Exception:
            log.debug("Failed to enable FFE Remote Config for prompt evaluation", exc_info=True)
            return False

    def _fetch_from_ff(
        self,
        prompt_id: str,
        targeting_key: Optional[str],
        attributes: dict[str, Any],
    ) -> Optional[ManagedPrompt]:
        """Evaluate a prompt via the FFE (Eppo UFC) path. Returns None on any failure."""
        from ddtrace.internal.settings import env as dd_env_settings

        env_val = dd_env_settings.get("DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED")
        if env_val is not None and env_val.lower() == "false":
            return None
        if env_val is None:
            dd_env_settings["DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED"] = "true"

        try:
            from ddtrace.internal.openfeature._config import _get_ffe_config
            from ddtrace.internal.openfeature._native import VariationType
            from ddtrace.internal.openfeature._native import resolve_flag
        except ImportError:
            log.debug("OpenFeature dependencies unavailable for FF prompt evaluation")
            return None

        # Ensure RC callback is registered so FFE config gets delivered
        rc_enabled = self._ensure_ffe_rc()

        try:
            ffe_config = _get_ffe_config()
            if ffe_config is None:
                if not rc_enabled:
                    log.debug("FFE Remote Config could not be enabled for prompt %s", prompt_id)
                return None

            flag_key = f"__llmobs__.prompt.{prompt_id}"
            ctx = {"targeting_key": targeting_key or "", "attributes": attributes or {}}
            details = resolve_flag(ffe_config, flag_key=flag_key, context=ctx, expected_type=VariationType.Object)
            if details is None or details.error_code is not None or details.variant is None:
                return None

            variant_value = details.value
            if not isinstance(variant_value, dict):
                return None

            return self._parse_prompt(variant_value, source="ff")
        except Exception:
            log.debug("FF prompt evaluation failed for %s", prompt_id, exc_info=True)
            return None

    def _fetch_from_registry(
        self, prompt_id: str, label: Optional[str], timeout: float
    ) -> tuple[Optional[ManagedPrompt], bool, str]:
        """Fetch from registry. Returns (prompt, not_found, reason)."""
        conn = None
        try:
            conn = get_connection(self._base_url, timeout=timeout)
            conn.request("GET", self._build_path(prompt_id, label), headers=self._headers)
            response = conn.getresponse()
            status = response.status

            body = response.read().decode("utf-8")

            if status == 200:
                return self._parse_prompt(body, source="registry", prompt_id=prompt_id, label=label), False, ""

            not_found = status == 404
            detail = extract_error_detail(body)
            if not_found:
                log.debug('Prompt not found: prompt_id=%s label=%s detail="%s"', prompt_id, label, detail)
            else:
                log.warning(
                    'Prompt fetch failed: prompt_id=%s label=%s status=%d detail="%s"', prompt_id, label, status, detail
                )
            return None, not_found, detail
        except Exception as e:
            log.warning("Prompt fetch exception: prompt_id=%s label=%s: %s", prompt_id, label, e)
            return None, False, str(e)
        finally:
            if conn is not None:
                conn.close()

    def _build_path(self, prompt_id: str, label: Optional[str]) -> str:
        """Build the absolute request path for fetching a prompt."""
        escaped_id = quote(prompt_id, safe="")
        if label:
            return f"{PROMPTS_ENDPOINT}/{escaped_id}?{urlencode({'label': label})}"
        return f"{PROMPTS_ENDPOINT}/{escaped_id}"

    @staticmethod
    def _parse_prompt(
        raw: Union[str, dict],
        source: Literal["registry", "cache", "fallback", "ff"],
        prompt_id: str = "",
        label: Optional[str] = None,
    ) -> Optional[ManagedPrompt]:
        try:
            if isinstance(raw, str):
                data = json.loads(raw)
                if not isinstance(data, dict):
                    log.warning("Failed to parse prompt response: expected object, got %s", type(data).__name__)
                    return None
            else:
                data = raw
            return ManagedPrompt(
                id=data.get("prompt_id", prompt_id),
                version=data.get("version", "unknown"),
                label=data.get("label", label),
                source=source,
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
        reason: str = "",
    ) -> ManagedPrompt:
        """Create a fallback prompt when fetch fails."""
        if fallback is None:
            message = "Prompt '{}' could not be fetched and no fallback was provided".format(prompt_id)
            if reason:
                message = "{}: {}".format(message, reason)
            raise ValueError(message)
        log.debug("Using user-provided fallback for prompt %s", prompt_id)
        return ManagedPrompt.from_fallback(prompt_id, fallback)
