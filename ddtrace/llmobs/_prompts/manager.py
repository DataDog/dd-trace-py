import atexit
import json
import threading
from typing import Any
from typing import Optional
from typing import Union
from urllib.parse import quote
from urllib.parse import urlencode

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
from ddtrace.llmobs.types import ChatMessage
from ddtrace.llmobs.types import DeletedPromptResponse
from ddtrace.llmobs.types import PromptAPIError
from ddtrace.llmobs.types import PromptAuthError
from ddtrace.llmobs.types import PromptConflictError
from ddtrace.llmobs.types import PromptFallback
from ddtrace.llmobs.types import PromptLabel
from ddtrace.llmobs.types import PromptNotFoundError
from ddtrace.llmobs.types import PromptResponse
from ddtrace.llmobs.types import PromptServerError
from ddtrace.llmobs.types import PromptValidationError
from ddtrace.llmobs.types import PromptVersionResponse


log = get_logger(__name__)

_STATUS_EXCEPTIONS: dict[int, type[PromptAPIError]] = {
    400: PromptValidationError,
    401: PromptAuthError,
    403: PromptAuthError,
    404: PromptNotFoundError,
    409: PromptConflictError,
}


class PromptManager:
    """Manages prompt retrieval and caching."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        app_key: str = "",
        cache_ttl: float = DEFAULT_PROMPTS_CACHE_TTL,
        timeout: float = DEFAULT_PROMPTS_TIMEOUT,
        file_cache_enabled: bool = True,
        cache_dir: Optional[str] = None,
    ) -> None:
        self._base_url = base_url if "://" in base_url else "https://" + base_url
        self._timeout = timeout
        self._app_key = app_key
        self._headers: dict[str, str] = {
            "DD-API-KEY": api_key,
            "X-Datadog-SDK-Language": "python",
        }
        self._cache_enabled = cache_ttl > 0

        self._hot_cache = HotCache(ttl_seconds=cache_ttl)
        self._warm_cache = WarmCache(enabled=file_cache_enabled, cache_dir=cache_dir, ttl_seconds=cache_ttl)

        self._refresh_threads: dict[str, threading.Thread] = {}
        self._refresh_lock = threading.Lock()
        if file_cache_enabled:
            atexit.register(self._wait_for_refreshes)

    def get_prompt(
        self,
        prompt_id: str,
        label: Optional[str] = None,
        fallback: PromptFallback = None,
    ) -> ManagedPrompt:
        """Retrieve a prompt template from the registry."""
        if not self._headers.get("DD-API-KEY"):
            raise PromptAuthError(0, "DD_API_KEY is required for prompt operations")
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

    def _http_request(
        self,
        method: str,
        path: str,
        body: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> tuple[int, str]:
        """Low-level HTTP transport. Returns (status, response_body)."""
        conn = None
        try:
            conn = get_connection(self._base_url, timeout=timeout or self._timeout)
            conn.request(method, path, body=body, headers=headers or self._headers)
            response = conn.getresponse()
            return response.status, response.read().decode("utf-8")
        finally:
            if conn is not None:
                conn.close()

    def _fetch_from_registry(
        self, prompt_id: str, label: Optional[str], timeout: float
    ) -> tuple[Optional[ManagedPrompt], bool, str]:
        """Fetch from registry. Returns (prompt, not_found, reason)."""
        try:
            status, body = self._http_request("GET", self._build_path(prompt_id, label), timeout=timeout)

            if status == 200:
                return self._parse_response(body, prompt_id, label), False, ""

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

    def _build_path(self, prompt_id: str, label: Optional[str]) -> str:
        """Build the absolute request path for fetching a prompt."""
        escaped_id = quote(prompt_id, safe="")
        if label:
            return f"{PROMPTS_ENDPOINT}/{escaped_id}?{urlencode({'label': label})}"
        return f"{PROMPTS_ENDPOINT}/{escaped_id}"

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

    # --- Prompt CRUD API methods ---

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict[str, Any]] = None,
        timeout: Optional[float] = None,
        require_app_key: bool = True,
    ) -> Any:
        if not self._headers.get("DD-API-KEY"):
            raise PromptAuthError(0, "DD_API_KEY is required for prompt operations")
        if require_app_key and not self._app_key:
            raise PromptAuthError(0, "DD_APP_KEY is required for prompt write operations")

        headers = {
            **self._headers,
            "Content-Type": "application/json",
        }
        if self._app_key:
            headers["DD-APPLICATION-KEY"] = self._app_key

        encoded_body = json.dumps(body).encode("utf-8") if body else None
        status, response_body = self._http_request(method, path, body=encoded_body, headers=headers, timeout=timeout)

        if 200 <= status < 300:
            return json.loads(response_body) if response_body else {}

        detail = extract_error_detail(response_body)
        exc_cls = _STATUS_EXCEPTIONS.get(status)
        if exc_cls is None:
            exc_cls = PromptServerError if status >= 500 else PromptAPIError
        raise exc_cls(status, detail)

    def create_prompt(
        self,
        prompt_id: str,
        template: list[ChatMessage],
        *,
        title: str = "",
        description: str = "",
        user_version: str = "",
        labels: Optional[list[PromptLabel]] = None,
    ) -> PromptResponse:
        body: dict[str, Any] = {"prompt_id": prompt_id, "template": template}
        if title:
            body["title"] = title
        if description:
            body["description"] = description
        if user_version:
            body["user_version"] = user_version
        if labels is not None:
            body["labels"] = labels
        result: PromptResponse = self._request("POST", PROMPTS_ENDPOINT, body=body)
        return result

    def create_prompt_version(
        self,
        prompt_id: str,
        template: list[ChatMessage],
        *,
        description: str = "",
        user_version: str = "",
        labels: Optional[list[PromptLabel]] = None,
    ) -> PromptVersionResponse:
        escaped_id = quote(prompt_id, safe="")
        body: dict[str, Any] = {"template": template}
        if description:
            body["description"] = description
        if user_version:
            body["user_version"] = user_version
        if labels is not None:
            body["labels"] = labels
        result: PromptVersionResponse = self._request("POST", f"{PROMPTS_ENDPOINT}/{escaped_id}/versions", body=body)
        return result

    def update_prompt(
        self,
        prompt_id: str,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
    ) -> PromptResponse:
        if title is None and description is None:
            raise PromptValidationError(0, "At least one of title or description must be provided")
        escaped_id = quote(prompt_id, safe="")
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if description is not None:
            body["description"] = description
        result: PromptResponse = self._request("PATCH", f"{PROMPTS_ENDPOINT}/{escaped_id}", body=body)
        return result

    def update_prompt_version(
        self,
        prompt_id: str,
        version: str,
        *,
        labels: Optional[list[PromptLabel]] = None,
        description: Optional[str] = None,
    ) -> PromptVersionResponse:
        if labels is None and description is None:
            raise PromptValidationError(0, "At least one of labels or description must be provided")
        escaped_id = quote(prompt_id, safe="")
        body: dict[str, Any] = {}
        if labels is not None:
            body["labels"] = labels
        if description is not None:
            body["description"] = description
        result: PromptVersionResponse = self._request(
            "PATCH", f"{PROMPTS_ENDPOINT}/{escaped_id}/versions/{version}", body=body
        )
        return result

    def delete_prompt(self, prompt_id: str) -> DeletedPromptResponse:
        escaped_id = quote(prompt_id, safe="")
        result: DeletedPromptResponse = self._request("DELETE", f"{PROMPTS_ENDPOINT}/{escaped_id}")
        self._hot_cache.evict_prompt(prompt_id)
        self._warm_cache.evict_prompt(prompt_id)
        return result

    def list_prompts(self, *, ml_app: Optional[str] = None) -> list[PromptResponse]:
        path = PROMPTS_ENDPOINT
        if ml_app:
            path = f"{PROMPTS_ENDPOINT}?{urlencode({'filter[ml_app]': ml_app})}"
        result: list[PromptResponse] = self._request("GET", path, require_app_key=False)
        return result

    def list_prompt_versions(self, prompt_id: str) -> list[PromptVersionResponse]:
        escaped_id = quote(prompt_id, safe="")
        result: list[PromptVersionResponse] = self._request(
            "GET", f"{PROMPTS_ENDPOINT}/{escaped_id}/versions", require_app_key=False
        )
        return result
