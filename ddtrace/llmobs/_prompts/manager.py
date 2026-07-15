import atexit
from dataclasses import dataclass
from dataclasses import field
import hashlib
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
from ddtrace.llmobs._constants import PromptSource
from ddtrace.llmobs._http import get_connection
from ddtrace.llmobs._prompts.cache import HotCache
from ddtrace.llmobs._prompts.cache import WarmCache
from ddtrace.llmobs._prompts.prompt import ManagedPrompt
from ddtrace.llmobs._prompts.utils import extract_error_detail
from ddtrace.llmobs._prompts.utils import extract_template
from ddtrace.llmobs.types import ChatMessage
from ddtrace.llmobs.types import DeletedPromptResponse
from ddtrace.llmobs.types import PromptAPIError
from ddtrace.llmobs.types import PromptAuthError
from ddtrace.llmobs.types import PromptConflictError
from ddtrace.llmobs.types import PromptFallback
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


def _normalize_response_ids(data: Any) -> Any:
    def normalize_item(item: Any) -> Any:
        if isinstance(item, dict) and "ID" in item and "id" not in item:
            item["id"] = item.pop("ID")
        return item

    if isinstance(data, list):
        return [normalize_item(item) for item in data]
    return normalize_item(data)


@dataclass(frozen=True)
class _PromptRequest:
    """Describes an HTTP prompt fetch: environment resolution or the static registry.

    The environment-resolved path (``DD_ENV`` set, no explicit ``label``) resolves env-scoped
    variants and targeting via ``POST .../{id}/resolve``. An explicit ``label`` (deprecated) or no
    ``DD_ENV`` at all falls back to the static ``GET .../{id}`` registry, which has no targeting.
    """

    prompt_id: str
    label: Optional[str] = None
    env: Optional[str] = None
    targeting_key: Optional[str] = None
    attributes: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def use_resolve(self) -> bool:
        return self.label is None and bool(self.env)

    @property
    def key(self) -> str:
        attrs = ""
        if self.attributes:
            blob = json.dumps(self.attributes, sort_keys=True, default=str)
            attrs = hashlib.sha1(blob.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
        return f"{self.prompt_id}:{self.label or ''}:{self.env or ''}:{self.targeting_key or ''}:{attrs}"

    @property
    def source(self) -> PromptSource:
        return PromptSource.RESOLVE if self.use_resolve else PromptSource.REGISTRY


class PromptManager:
    """Manages prompt retrieval and caching."""

    _FFE_DOMAIN = "datadog-llmobs-prompts"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        app_key: str = "",
        cache_ttl: float = DEFAULT_PROMPTS_CACHE_TTL,
        timeout: float = DEFAULT_PROMPTS_TIMEOUT,
        file_cache_enabled: bool = True,
        cache_dir: Optional[str] = None,
        agentless: bool = True,
    ) -> None:
        self._base_url = base_url if "://" in base_url else "https://" + base_url
        self._timeout = timeout
        self._agentless = agentless
        self._api_key = api_key
        # App key or Service Access Token (sent as DD-APPLICATION-KEY). Required to satisfy the
        # RBAC-gated /resolve endpoint; a SAT rides the same header per Datadog's token migration path.
        self._app_key = app_key
        self._headers: dict[str, str] = {
            "X-Datadog-SDK-Language": "python",
        }
        if api_key:
            self._headers["DD-API-KEY"] = api_key
        self._cache_enabled = cache_ttl > 0

        self._hot_cache = HotCache(ttl_seconds=cache_ttl)
        self._warm_cache = WarmCache(enabled=file_cache_enabled, cache_dir=cache_dir, ttl_seconds=cache_ttl)

        self._refresh_threads: dict[str, threading.Thread] = {}
        self._refresh_lock = threading.Lock()
        self._ffe_lock = threading.Lock()
        self._ffe_rc_enabled = False
        self._ffe_provider_set = False
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
        """Retrieve a prompt template from the registry or by environment resolution."""
        if not self._headers.get("DD-API-KEY"):
            raise PromptAuthError(0, "DD_API_KEY is required for prompt operations")
        if label is not None and (targeting_key is not None or attributes):
            warnings.warn(
                "get_prompt() received 'label' alongside 'targeting_key' or other attributes. "
                "'label' routes to the HTTP path which does not support targeting; the extra "
                "arguments will be ignored. Drop 'label' to resolve the prompt by environment.",
                UserWarning,
                stacklevel=2,
            )

        dd_env = config.env
        if label is None and dd_env and not self._agentless:
            prompt = self._fetch_from_ff(prompt_id, targeting_key, attributes)
            if prompt is not None:
                telemetry.record_prompt_source(PromptSource.FF)
                return prompt
            # FF is the only positive hit. NOT_READY/NO_FLAG/DISABLED/ERROR all fall through to
            # the HTTP /resolve floor, which resolves the same env-scoped variant server-side.

        if label is not None:
            req = _PromptRequest(prompt_id=prompt_id, label=label)
        elif dd_env:
            req = _PromptRequest(prompt_id=prompt_id, env=dd_env, targeting_key=targeting_key, attributes=attributes)
        else:
            req = _PromptRequest(prompt_id=prompt_id)
        return self._get_prompt_http(req, fallback=fallback)

    def _get_prompt_http(self, req: _PromptRequest, fallback: PromptFallback = None) -> ManagedPrompt:
        """Retrieve a prompt via HTTP (environment resolution ``/resolve`` or the static registry)."""
        if self._cache_enabled:
            # Try hot cache (in-memory)
            prompt = self._try_cache(self._hot_cache, req, PromptSource.HOT_CACHE)
            if prompt is not None:
                return prompt

            # Try warm cache (file-based). Skipped for per-subject resolve results, which are
            # high-cardinality and disposable - persisting them would flood disk.
            if not req.use_resolve:
                prompt = self._try_cache(self._warm_cache, req, PromptSource.WARM_CACHE, populate_hot=True)
                if prompt is not None:
                    return prompt

        # Try sync fetch
        fetched_prompt, reason = self._fetch_and_cache(req, evict_on_not_found=False)
        if fetched_prompt is not None:
            telemetry.record_prompt_source(req.source)
            return fetched_prompt

        # Fall back to user-provided or empty prompt
        telemetry.record_prompt_source(PromptSource.FALLBACK)
        return self._create_fallback_prompt(req.prompt_id, fallback, reason=reason)

    def _try_cache(
        self,
        cache: Union[HotCache, WarmCache],
        req: _PromptRequest,
        source_name: PromptSource,
        populate_hot: bool = False,
    ) -> Optional[ManagedPrompt]:
        """Try to get prompt from cache, trigger refresh if stale."""
        result = cache.get(req.key)
        if result is None:
            return None
        prompt, is_stale = result
        if populate_hot:
            self._hot_cache.set(req.key, prompt)
        if is_stale:
            self._trigger_background_refresh(req)
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
        if not self._headers.get("DD-API-KEY"):
            raise PromptAuthError(0, "DD_API_KEY is required for prompt operations")
        req = _PromptRequest(prompt_id=prompt_id, label=label, env=None if label is not None else config.env)
        prompt, _ = self._fetch_and_cache(req, evict_on_not_found=True)
        return prompt

    def _update_caches(self, req: _PromptRequest, prompt: ManagedPrompt) -> None:
        """Cache the prompt with source='cache'.

        Hot cache always; warm (file) cache only for low-cardinality static/label fetches. Per-subject
        resolve results are disposable and would otherwise flood disk with tiny files.
        """
        if not self._cache_enabled:
            return
        cached_prompt = prompt._with_source("cache")
        self._hot_cache.set(req.key, cached_prompt)
        if not req.use_resolve:
            self._warm_cache.set(req.key, cached_prompt)

    def _evict_caches(self, key: str) -> None:
        """Remove a prompt from both caches."""
        if not self._cache_enabled:
            return
        self._hot_cache.delete(key)
        self._warm_cache.delete(key)

    def _fetch_and_cache(
        self,
        req: _PromptRequest,
        evict_on_not_found: bool = False,
    ) -> tuple[Optional[ManagedPrompt], str]:
        """Fetch a prompt and update caches."""
        prompt, not_found, reason = self._fetch_http(req, timeout=self._timeout)

        if prompt is not None:
            self._update_caches(req, prompt)
            return prompt, ""

        if not_found:
            telemetry.record_prompt_fetch_error("NotFound")
            if evict_on_not_found:
                self._evict_caches(req.key)
        else:
            telemetry.record_prompt_fetch_error("FetchError")

        return None, reason

    def _trigger_background_refresh(self, req: _PromptRequest) -> None:
        """Trigger a background refresh if not already in progress."""
        key = req.key

        def run_refresh():
            try:
                self._background_refresh(req)
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
            log.debug("Failed to start background refresh thread for prompt %s", req.prompt_id)

    def _background_refresh(self, req: _PromptRequest) -> None:
        """Refresh a prompt in the background."""
        self._fetch_and_cache(req, evict_on_not_found=True)

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

    def _ensure_ffe_rc(self) -> None:
        """Lazily enable FFE Remote Config so flag configurations are delivered."""
        with self._ffe_lock:
            if self._ffe_rc_enabled:
                return
            try:
                from ddtrace.internal.openfeature._remoteconfiguration import enable_featureflags_rc

                enable_featureflags_rc()
                self._ffe_rc_enabled = True
            except Exception:
                log.debug("Failed to enable FFE Remote Config for prompt evaluation", exc_info=True)

    def _ensure_ffe_provider(self) -> None:
        """Lazily register the DataDog OpenFeature provider (non-blocking)."""
        with self._ffe_lock:
            if self._ffe_provider_set:
                return
            try:
                from openfeature import api

                from ddtrace.internal.openfeature._provider import DataDogProvider

                # Non-blocking: registers for RC callbacks without waiting for config delivery.
                # Scoped to our own domain so we don't replace the app's default provider.
                api.set_provider(DataDogProvider(initialization_timeout=0), self._FFE_DOMAIN)
                self._ffe_provider_set = True
            except Exception:
                log.debug("Failed to register OpenFeature provider", exc_info=True)

    def _fetch_from_ff(
        self,
        prompt_id: str,
        targeting_key: Optional[str],
        attributes: dict[str, Any],
    ) -> Optional[ManagedPrompt]:
        """Evaluate a prompt via the OpenFeature SDK.

        Returns a prompt only on a positive FF hit. All other outcomes (not ready, disabled,
        no flag, error) return None and fall through to the HTTP floor.
        """
        from ddtrace.internal.settings.openfeature import config as ffe_config

        if not ffe_config.experimental_flagging_provider_enabled:
            return None

        try:
            from openfeature import api
            from openfeature.evaluation_context import EvaluationContext
            from openfeature.exception import ErrorCode
        except ImportError:
            log.debug("OpenFeature SDK unavailable for FF prompt evaluation")
            return None

        self._ensure_ffe_rc()
        self._ensure_ffe_provider()

        try:
            flag_key = f"__llmobs__.prompt.{prompt_id}"
            context = EvaluationContext(
                targeting_key=targeting_key,
                attributes=attributes,
            )
            client = api.get_client(self._FFE_DOMAIN)
            details = client.get_object_details(flag_key, {}, context)

            if details.error_code == ErrorCode.PROVIDER_NOT_READY:
                return None

            value = details.value
            if isinstance(value, dict) and value:
                return self._parse_prompt(value, source="ff")

            return None
        except Exception:
            log.debug("FF prompt evaluation failed for %s", prompt_id, exc_info=True)
            return None

    def _fetch_http(self, req: _PromptRequest, timeout: float) -> tuple[Optional[ManagedPrompt], bool, str]:
        """Fetch a prompt over HTTP. Returns (prompt, not_found, reason)."""
        if not self._api_key:
            return None, False, "DD_API_KEY is required for the Prompt Registry"
        if req.use_resolve and not self._app_key:
            return None, False, "an app key or Service Access Token is required to resolve prompts for an environment"

        scope = req.label or req.env
        conn = None
        try:
            conn = get_connection(self._base_url, timeout=timeout)
            escaped_id = quote(req.prompt_id, safe="")
            if req.use_resolve:
                attrs: dict[str, Any] = {"env": req.env or ""}
                if req.targeting_key is not None:
                    attrs["targeting_key"] = req.targeting_key
                if req.attributes:
                    attrs["context"] = req.attributes
                path = f"{PROMPTS_ENDPOINT}/{escaped_id}/resolve"
                body = json.dumps({"data": {"type": "prompt_resolve_requests", "attributes": attrs}})
                headers = {**self._headers, "Content-Type": "application/json", "DD-APPLICATION-KEY": self._app_key}
                conn.request("POST", path, body=body, headers=headers)
            else:
                conn.request("GET", self._build_path(req.prompt_id, req.label), headers=self._headers)
            response = conn.getresponse()
            status = response.status

            body = response.read().decode("utf-8")

            if status == 200:
                source: Literal["registry", "resolve"] = "resolve" if req.use_resolve else "registry"
                prompt = self._parse_prompt(body, source=source, label=req.label)
                return prompt, False, ""

            not_found = status == 404
            detail = extract_error_detail(body)
            if not_found:
                log.debug('Prompt not found: prompt_id=%s scope=%s detail="%s"', req.prompt_id, scope, detail)
            else:
                log.warning(
                    'Prompt fetch failed: prompt_id=%s scope=%s status=%d detail="%s"',
                    req.prompt_id,
                    scope,
                    status,
                    detail,
                )
            return None, not_found, detail
        except Exception as e:
            log.warning("Prompt fetch exception: prompt_id=%s scope=%s: %s", req.prompt_id, scope, e)
            return None, False, str(e)

    def _build_path(self, prompt_id: str, label: Optional[str]) -> str:
        """Build the absolute request path for fetching a prompt from the static registry."""
        escaped_id = quote(prompt_id, safe="")
        if label:
            return f"{PROMPTS_ENDPOINT}/{escaped_id}?{urlencode({'label': label})}"
        return f"{PROMPTS_ENDPOINT}/{escaped_id}"

    @staticmethod
    def _parse_prompt(
        raw: Union[str, dict[str, Any]],
        source: Literal["registry", "ff", "resolve"],
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
            prompt_id = data.get("prompt_id")
            if not prompt_id:
                log.warning("Failed to parse prompt response: missing prompt_id")
                return None
            version = data.get("version")
            if not version:
                log.warning("Failed to parse prompt response: missing version")
                return None
            return ManagedPrompt(
                id=prompt_id,
                version=version,
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

    # --- Prompt CRUD API methods ---

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict[str, Any]] = None,
        timeout: Optional[float] = None,
        require_app_key: bool = True,
    ) -> Any:
        try:
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
            try:
                status, response_body = self._http_request(
                    method, path, body=encoded_body, headers=headers, timeout=timeout
                )
            except Exception as e:
                raise PromptAPIError(0, str(e))

            if 200 <= status < 300:
                if not response_body:
                    return {}
                try:
                    return _normalize_response_ids(json.loads(response_body))
                except (json.JSONDecodeError, ValueError):
                    raise PromptServerError(status, "invalid JSON in response body")

            detail = extract_error_detail(response_body)
            exc_cls = _STATUS_EXCEPTIONS.get(status)
            if exc_cls is None:
                exc_cls = PromptServerError if status >= 500 else PromptAPIError
            raise exc_cls(status, detail)
        except PromptAPIError as e:
            telemetry.record_prompt_crud_error(method, type(e).__name__, e.status)
            raise

    def _evict_prompt_caches(self, prompt_id: str) -> None:
        self._hot_cache.evict_prompt(prompt_id)
        self._warm_cache.evict_prompt(prompt_id)

    def create_prompt(
        self,
        prompt_id: str,
        template: list[ChatMessage],
        *,
        title: str = "",
        description: str = "",
        user_version: str = "",
        labels: Optional[list[str]] = None,
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
        self._evict_prompt_caches(prompt_id)
        return result

    def create_prompt_version(
        self,
        prompt_id: str,
        template: list[ChatMessage],
        *,
        description: str = "",
        user_version: str = "",
        labels: Optional[list[str]] = None,
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
        self._evict_prompt_caches(prompt_id)
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
        self._evict_prompt_caches(prompt_id)
        return result

    def update_prompt_version(
        self,
        prompt_id: str,
        version: int,
        *,
        labels: Optional[list[str]] = None,
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
        self._evict_prompt_caches(prompt_id)
        return result

    def delete_prompt(self, prompt_id: str) -> DeletedPromptResponse:
        escaped_id = quote(prompt_id, safe="")
        result: DeletedPromptResponse = self._request("DELETE", f"{PROMPTS_ENDPOINT}/{escaped_id}")
        self._evict_prompt_caches(prompt_id)
        return result

    def list_prompts(self) -> list[PromptResponse]:
        result: list[PromptResponse] = self._request("GET", PROMPTS_ENDPOINT, require_app_key=False)
        return result

    def list_prompt_versions(self, prompt_id: str) -> list[PromptVersionResponse]:
        escaped_id = quote(prompt_id, safe="")
        result: list[PromptVersionResponse] = self._request(
            "GET", f"{PROMPTS_ENDPOINT}/{escaped_id}/versions", require_app_key=False
        )
        return result
