"""LiteLLM proxy adapter. End-user claims stay distinct from gateway authentication."""

from collections import ChainMap
from collections import OrderedDict
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
import json
import os
from pathlib import Path
import time
from typing import Any
from typing import Callable
from typing import Iterable
from typing import Optional
import uuid

from litellm.integrations.custom_logger import CustomLogger

from ddtrace import tracer
from ddtrace.contrib.internal.litellm._gateway_metadata import cache_tags
from ddtrace.contrib.internal.litellm._gateway_metadata import request_tags
from ddtrace.contrib.internal.litellm._gateway_metadata import response_tags
from ddtrace.contrib.internal.litellm._gateway_metadata import route_tags
from ddtrace.contrib.internal.litellm._gateway_usage import DatadogSink
from ddtrace.contrib.internal.litellm._gateway_usage import Usage
from ddtrace.contrib.internal.litellm._gateway_usage import UsageRecord
from ddtrace.contrib.internal.litellm._gateway_usage import get
from ddtrace.contrib.internal.litellm._gateway_usage import label
from ddtrace.contrib.internal.litellm._gateway_usage import normalize_usage
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.env import dd_environ
from ddtrace.trace import Context


log = get_logger(__name__)
CORRELATION_FIELD = "_dd_gateway_attribution_token"
METADATA = ("metadata", "litellm_metadata")
SUPPORTED_CALLS = {
    "completion",
    "acompletion",
    "text_completion",
    "atext_completion",
    "anthropic_messages",
    "responses",
    "aresponses",
    "embedding",
    "aembedding",
}


@dataclass
class Pending:
    start: float
    created: float
    tags: dict[str, str]
    parent: Optional[Context]
    multimodal: bool = False
    deployment: Optional[str] = None
    attempts: int = 0
    route: dict[str, str] = field(default_factory=dict)
    effective: dict[str, str] = field(default_factory=dict)


def _has_nontext_input(data: dict[str, Any]) -> bool:
    if data.get("audio") or "audio" in (data.get("modalities") or []):
        return True
    for key in ("messages", "input"):
        items = data.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            content = get(item, "content", [])
            blocks = [item] + (content if isinstance(content, list) else [])
            if any(
                get(block, "type")
                in {
                    "image_url",
                    "input_image",
                    "image",
                    "input_audio",
                    "audio",
                    "file",
                    "input_file",
                    "document",
                    "video",
                }
                for block in blocks
            ):
                return True
    return False


# LiteLLM is optional and not installed in the lint environment.
class GatewayAttribution(CustomLogger):  # type: ignore[misc]
    """Record gateway users and usage in APM, without prompt or response text.

    :param capture_email: Include authenticated user email. Defaults to ``False``.
    :param capture_end_user: Include LiteLLM's end-user ID as unverified context
        and use it when the authenticated user ID is missing. Defaults to ``True``.
    :param auth_metadata_keys: User metadata fields to copy, such as ``cost_center``.
        Only authenticated user metadata is read, never client-supplied request fields.
    :raises ValueError: If configuration contains invalid fields or values.
    """

    def __init__(
        self,
        *,
        capture_email: bool = False,
        capture_end_user: bool = True,
        auth_metadata_keys: Iterable[str] = (),
    ) -> None:
        # Older SDKs accept only message_logging; newer proxies consult the inverted flag.
        super().__init__(message_logging=False)
        self.turn_off_message_logging = True
        if (
            not isinstance(capture_email, bool)
            or not isinstance(capture_end_user, bool)
            or isinstance(auth_metadata_keys, str)
        ):
            raise ValueError("Invalid gateway attribution privacy configuration")
        metadata_keys = tuple(auth_metadata_keys)
        for key in metadata_keys:
            if (
                label(key) is None
                or not key.replace("_", "").isalnum()
                or any(word in key.lower() for word in ("key", "token", "secret", "password", "authorization"))
            ):
                raise ValueError("Only non-secret auth metadata keys may be selected")
        self._capture_email = capture_email
        self._capture_end_user = capture_end_user
        self._auth_metadata_keys = metadata_keys
        self._sink: Callable[[UsageRecord], None] = DatadogSink()
        self._max_pending = 10000
        self._pending_ttl = 3600.0
        self._pending: OrderedDict[str, Pending] = OrderedDict()
        self._lock = forksafe.Lock()
        self._pid = os.getpid()

    def _ensure_process(self) -> None:
        # Called with the fork-safe lock held; a worker never exports its parent's requests.
        pid = os.getpid()
        if self._pid != pid:
            self._pending.clear()
            self._pid = pid

    async def async_pre_call_hook(
        self, user_api_key_dict: Any, cache: Any, data: dict[str, Any], call_type: str
    ) -> Optional[dict[str, Any]]:
        try:
            return self._start(user_api_key_dict, data, call_type)
        except Exception:
            log.warning("Gateway attribution hook failed; usage coverage is incomplete")
            return None

    def _start(self, user_api_key_dict: Any, data: dict[str, Any], call_type: str) -> Optional[dict[str, Any]]:
        # Never let client metadata supply our process-local correlation token.
        for key in METADATA:
            if isinstance(data.get(key), dict):
                data[key].pop(CORRELATION_FIELD, None)
        if call_type not in SUPPORTED_CALLS:
            return None
        tags = {
            "ai.gateway": "litellm",
            "ai.attribution.schema": "1",
            "ai.attribution.coverage": "logical_request",
            "ai.request.id": uuid.uuid4().hex,
            "ai.operation": call_type,
            "ai.timezone": "UTC",
        }
        tags.update(request_tags(data, "ai.request"))
        tags.update(cache_tags(data, "ai.request"))
        for source, target in (
            ("user_id", "usr.id"),
            ("team_id", "team.id"),
            ("org_id", "ai.gateway.org_id"),
        ):
            if value := label(get(user_api_key_dict, source)):
                tags[target] = value
        if self._capture_email and (email := label(get(user_api_key_dict, "user_email"))):
            tags["usr.email"] = email
        tags["ai.identity.source"] = "gateway_auth" if "usr.id" in tags else "unknown"
        # AIDEV-NOTE: LiteLLM resolves end_user_id from supported headers/body fields
        # and may filter it using its own policy. Do not re-read raw fields when it
        # is absent, or mistake an end-user claim for an authenticated principal.
        if self._capture_end_user and (end_user := label(get(user_api_key_dict, "end_user_id"))):
            # Some clients put JSON containing device/session metadata here, not a user ID.
            if not end_user.startswith(("{", "[")):
                tags["ai.end_user.id"] = end_user
                tags["ai.end_user.trust"] = "unverified"
                if "usr.id" not in tags:
                    tags["usr.id"] = end_user
                    tags["ai.identity.source"] = "litellm_end_user"
        for key in self._auth_metadata_keys:
            if value := label(get(get(user_api_key_dict, "metadata"), key)):
                tags[f"ai.enrichment.{key}"] = value
        state = Pending(
            time.time(),
            time.monotonic(),
            tags,
            tracer.current_trace_context(),
            multimodal=_has_nontext_input(data),
        )
        token = uuid.uuid4().hex
        expired = []
        with self._lock:
            self._ensure_process()
            while self._pending:
                oldest = next(iter(self._pending.values()))
                if state.created - oldest.created < self._pending_ttl:
                    break
                expired.append(self._pending.popitem(last=False)[1])
            while len(self._pending) >= self._max_pending:
                expired.append(self._pending.popitem(last=False)[1])
            self._pending[token] = state
        for old in expired:
            self._incomplete(old, "callback_missing_or_evicted")
        for key in METADATA:
            if data.get(key) is None:
                data[key] = {}
            if isinstance(data[key], dict):
                data[key][CORRELATION_FIELD] = token
        return data

    def _token(self, data: Any) -> Optional[str]:
        params = get(data, "litellm_params", {})
        tokens = {
            token
            for container in (data, params)
            for key in METADATA
            if isinstance(token := get(get(container, key), CORRELATION_FIELD), str)
        }
        if len(tokens) != 1:
            return None
        return tokens.pop()

    async def async_pre_call_deployment_hook(self, kwargs: dict[str, Any], call_type: str) -> None:
        # AIDEV-NOTE: LiteLLM calls this AFTER routing overwrites model_info with the selected
        # deployment, not at the untrusted ingress metadata boundary.
        try:
            token = self._token(kwargs)
            route = route_tags(kwargs)
            with self._lock:
                self._ensure_process()
                state = self._pending.get(token) if token is not None else None
                if state:
                    state.deployment = label(get(kwargs.get("model_info"), "id"))
                    state.attempts += 1
                    state.route = route
                    state.effective = {}  # Do not reuse an earlier failed deployment's settings.
        except Exception:
            log.warning("Gateway route attribution failed; usage coverage is incomplete")

    def log_pre_api_call(self, model: Any, messages: Any, kwargs: dict[str, Any]) -> None:
        # LiteLLM has now applied defaults and provider transformations. Inspect only
        # selected settings in the outgoing payload; never retain messages or kwargs.
        try:
            token = self._token(kwargs)
            additional = kwargs.get("additional_args")
            payload = get(additional, "complete_input_dict")
            effective = request_tags(payload, "ai.effective")
            effective.update(cache_tags(payload, "ai.effective"))
            if value := label(get(payload, "model")):
                effective["ai.effective.model"] = value
            params = kwargs.get("litellm_params")
            route = ChainMap(
                additional if isinstance(additional, dict) else {},
                kwargs,
                params if isinstance(params, dict) else {},
            )
            # OpenAI's async adapter puts per-request scope headers in SDK options,
            # while its synchronous adapter also exposes them in additional_args.
            extra_headers = get(payload, "extra_headers")
            headers = get(additional, "headers")
            outgoing_headers = ChainMap(
                extra_headers if isinstance(extra_headers, dict) else {},
                headers if isinstance(headers, dict) else {},
            )
            with self._lock:
                self._ensure_process()
                state = self._pending.get(token) if token is not None else None
                if state:
                    state.route = route_tags(route, state.route, headers=outgoing_headers)
                    state.effective = effective
        except Exception:
            log.warning("Gateway request attribution failed; usage coverage is incomplete")

    def _take(self, data: Any) -> Optional[Pending]:
        with self._lock:
            self._ensure_process()
            token = self._token(data)
            state = self._pending.pop(token, None) if token is not None else None
        return state

    def _emit(self, record: UsageRecord) -> None:
        try:
            self._sink(record)
        except Exception:
            # Do not log exception strings or payloads: they can contain credentials/content.
            log.warning("Gateway attribution span export failed; usage coverage is incomplete")

    def _incomplete(self, state: Pending, reason: str) -> None:
        tags = dict(
            state.tags,
            **{
                "ai.attribution.status": "incomplete",
                "ai.attribution.issues": reason,
                "ai.usage.source": "unavailable",
                "ai.request.outcome": "unknown",
            },
        )
        tags.update(state.route)
        tags.update(state.effective)
        self._emit(UsageRecord(state.start, time.time(), tags, Usage(), state.parent))

    async def async_log_success_event(
        self, kwargs: dict[str, Any], response_obj: Any, start_time: Optional[datetime], end_time: Optional[datetime]
    ) -> None:
        state = self._take(kwargs)
        if state is None:
            return  # SDK calls without proxy authentication and repeated callbacks are ignored.
        try:
            self._success(state, kwargs, response_obj, end_time)
        except Exception:
            self._incomplete(state, "unsupported_callback_shape")

    def _success(self, state: Pending, kwargs: dict[str, Any], response: Any, end_time: Optional[datetime]) -> None:
        hidden = get(response, "_hidden_params", {})
        deployment = label(get(hidden, "model_id")) or state.deployment
        usage = normalize_usage(get(response, "usage"), state.tags["ai.operation"])
        if state.multimodal:
            usage.quantities.clear()
            usage.issues.add("multimodal_partition_unsupported")
        issues = set(usage.issues)
        tags = dict(state.tags)
        if not deployment or not state.deployment or deployment == state.deployment:
            tags.update(state.route)
            tags.update(state.effective)
        else:
            issues.add("selected_route_metadata_mismatch")
        tags["ai.request.outcome"] = "success"
        tags["ai.usage.source"] = "litellm_normalized"
        usage.diagnostics["attempts"] = state.attempts
        if state.attempts > 1:
            issues.add("additional_attempt_usage_unknown")
        if kwargs.get("stream"):
            tags["ai.usage.source"] = "litellm_normalized_may_estimate"
            issues.add("stream_usage_provenance_unverified")
        if deployment:
            tags["ai.gateway.deployment_id"] = deployment
        if tags["ai.identity.source"] != "gateway_auth":
            issues.add("authenticated_user_unknown")
        # Keep the provider-returned raw model, including pricing-relevant suffixes.
        model = label(get(response, "model"), 2048)
        if model:
            tags["ai.response.model"] = model
        route_model = tags.get("ai.route.model")
        if selected_model := model or route_model:
            tags["ai.model"] = selected_model
            tags["ai.model.source"] = "response" if model else "selected_route"
        else:
            issues.add("model_unknown")
        tags.update(response_tags(response))
        if provider := label(get(hidden, "custom_llm_provider")):
            tags["ai.model.provider"] = provider
        if response_id := label(get(response, "id")):
            tags["ai.response.id"] = response_id
        cache_hit = kwargs.get("cache_hit")
        if cache_hit is None:
            cache_hit = get(kwargs.get("standard_logging_object"), "cache_hit")
        if cache_hit is True:
            usage = Usage()
            tags["ai.request.outcome"] = "gateway_cache_hit"
            tags["ai.usage.source"] = "gateway_cache"
            issues.add("no_new_provider_usage")
        tags["ai.attribution.status"] = "incomplete" if issues else "observed"
        # 'observed' means dimensions collected, NOT invoice-exact or safe to bill.
        if issues:
            tags["ai.attribution.issues"] = ",".join(sorted(issues))
        end = end_time.timestamp() if end_time is not None and end_time.tzinfo else time.time()
        self._emit(UsageRecord(state.start, end, tags, usage, state.parent))

    async def async_post_call_failure_hook(
        self,
        request_data: dict[str, Any],
        original_exception: Exception,
        user_api_key_dict: Any,
        traceback_str: Optional[str] = None,
    ) -> None:
        state = self._take(request_data)
        if state:
            tags = dict(
                state.tags,
                **{
                    "ai.attribution.status": "incomplete",
                    "ai.attribution.issues": "failed_request_usage_unknown",
                    "ai.request.outcome": "error",
                    "ai.usage.source": "unavailable",
                },
            )
            tags.update(state.route)
            tags.update(state.effective)
            self._emit(UsageRecord(state.start, time.time(), tags, Usage(), state.parent, error=True))

    def close(self) -> None:
        """Report unfinished requests before calling ``tracer.shutdown()``.

        The configured callback registers this method at process exit. Call it explicitly
        when managing the callback lifecycle yourself. No background worker is created.
        """
        with self._lock:
            self._ensure_process()
            pending = list(self._pending.values())
            self._pending.clear()
        for state in pending:
            self._incomplete(state, "callback_missing_at_shutdown")


def configured_callback() -> GatewayAttribution:
    """Read only operator configuration, failing closed on invalid attribution settings."""
    path = dd_environ.get("DD_LITELLM_GATEWAY_ATTRIBUTION_CONFIG")
    if not path:
        return GatewayAttribution()
    try:
        with Path(path).open() as config_file:
            config = json.load(config_file)
        if not isinstance(config, dict):
            raise ValueError("Invalid gateway attribution configuration")
        return GatewayAttribution(**config)
    except (OSError, TypeError, ValueError):
        log.warning("Invalid gateway attribution configuration; optional identity enrichment disabled")
        return GatewayAttribution(capture_end_user=False)
