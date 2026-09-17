"""LiteLLM proxy adapter. No identity or billing scope is trusted from client metadata."""

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
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
from ddtrace.contrib.internal.litellm._gateway_usage import BillingScope
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
}


@dataclass
class Pending:
    start: float
    created: float
    tags: dict[str, str]
    parent: Optional[Context]
    dynamic_credentials: bool
    multimodal: bool = False
    deployment: Optional[str] = None
    attempts: int = 0


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
    """Collect content-free APM usage spans for authenticated LiteLLM proxy requests.

    :param billing_scopes: Mapping from router ``model_info.id`` to non-secret billing
        fields: ``provider``, ``account_id``, ``product``, and optionally ``project_id``,
        ``resource_id``, ``api_key_id``, ``geography``, ``mode``, and ``model``.
    :param capture_email: Include authenticated user email. Defaults to ``False``.
    :param auth_metadata_keys: Non-secret keys to copy from authenticated user metadata.
        Request-supplied metadata is never used for attribution.
    :raises ValueError: If configuration contains invalid fields or values.
    """

    def __init__(
        self,
        billing_scopes: Optional[Mapping[str, Mapping[str, str]]] = None,
        *,
        capture_email: bool = False,
        auth_metadata_keys: Iterable[str] = (),
    ) -> None:
        super().__init__(turn_off_message_logging=True)
        if not isinstance(capture_email, bool) or isinstance(auth_metadata_keys, str):
            raise ValueError("Invalid gateway attribution privacy configuration")
        metadata_keys = tuple(auth_metadata_keys)
        for key in metadata_keys:
            if (
                label(key) is None
                or not key.replace("_", "").isalnum()
                or any(word in key.lower() for word in ("key", "token", "secret", "password", "authorization"))
            ):
                raise ValueError("Only non-secret auth metadata keys may be allowlisted")
        if billing_scopes is not None and not isinstance(billing_scopes, Mapping):
            raise ValueError("Billing scopes must be a mapping")
        self._routes: dict[str, BillingScope] = {}
        for deployment, scope in (billing_scopes or {}).items():
            if label(deployment) is None or not isinstance(scope, Mapping):
                raise ValueError("Invalid gateway billing scope")
            try:
                self._routes[deployment] = BillingScope(**scope)
            except (TypeError, ValueError):
                # Do not include configuration values or exception details in logs.
                raise ValueError("Invalid non-secret gateway billing fields") from None
        self._capture_email = capture_email
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
        # Overwrite both namespaces: request metadata is not an identity channel.
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
        for key in self._auth_metadata_keys:
            if value := label(get(get(user_api_key_dict, "metadata"), key)):
                tags[f"ai.enrichment.{key}"] = value
        # Never substitute a provider user field, email header, virtual-key hash, or
        # client end_user_id for the authenticated principal (which may be a service).
        dynamic_credentials = any(
            key in data
            for key in (
                "api_key",
                "api_base",
                "base_url",
                "litellm_credential_name",
                "aws_access_key_id",
                "aws_secret_access_key",
                "aws_session_token",
                "vertex_credentials",
                "azure_ad_token",
            )
        )
        state = Pending(
            time.time(),
            time.monotonic(),
            tags,
            tracer.current_trace_context(),
            dynamic_credentials,
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
        token = self._token(kwargs)
        with self._lock:
            self._ensure_process()
            state = self._pending.get(token) if token is not None else None
            if state:
                state.deployment = label(get(kwargs.get("model_info"), "id"))
                state.attempts += 1

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
        scope = self._routes.get(deployment) if deployment is not None and not state.dynamic_credentials else None
        usage = normalize_usage(get(response, "usage"), state.tags["ai.operation"])
        if state.multimodal:
            usage.quantities.clear()
            usage.issues.add("multimodal_partition_unsupported")
        issues = set(usage.issues)
        tags = dict(state.tags)
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
        if scope:
            tags.update(scope.tags())
        else:
            issues.add("billing_scope_unknown")
        if state.dynamic_credentials:
            issues.add("client_credentials_or_endpoint")
        if "usr.id" not in tags:
            issues.add("authenticated_user_unknown")
        # Keep the provider-returned raw model, including pricing-relevant suffixes.
        model = label(get(response, "model"))
        if model:
            tags["ai.response.model"] = model
        billed_model = scope.model if scope and scope.model else model
        if billed_model:
            tags["ai.model"] = billed_model
            tags["ai.model.source"] = "operator_mapping" if scope and scope.model else "response"
        else:
            issues.add("model_unknown")
        # Only a response-resolved tier is evidence. A requested 'auto' tier is not.
        tier = label(get(response, "service_tier")) or label(get(get(response, "usage"), "service_tier"))
        if tier and tier != "auto":
            tags["ai.billing.mode"] = tier
        if "ai.billing.mode" not in tags:
            issues.add("billed_mode_unknown")
        if "ai.billing.geography" not in tags:
            issues.add("billing_geography_unknown")
        for source, target in (
            ("speed", "ai.observed.speed"),
            ("inference_geo", "ai.observed.inference_geo"),
        ):
            if value := label(get(get(response, "usage"), source)):
                tags[target] = value
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
        log.warning("Invalid gateway attribution configuration; billing and optional identity enrichment disabled")
        return GatewayAttribution()
