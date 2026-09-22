"""Content-free usage records and conservative LiteLLM usage normalization."""

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
import os
from typing import Any
from typing import Optional

from ddtrace.internal.dogstatsd import get_dogstatsd_client
from ddtrace.internal.native._native import _ai_usage_context_bucket
from ddtrace.internal.native._native import _ai_usage_label
from ddtrace.internal.native._native import _normalize_ai_usage
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.vendor.dogstatsd import DogStatsd


def get(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


def label(value: Any, max_length: int = 256) -> Optional[str]:
    return _ai_usage_label(value, max_length)


@dataclass
class Usage:
    quantities: dict[str, int] = field(default_factory=dict)
    diagnostics: dict[str, float] = field(default_factory=dict)
    issues: set[str] = field(default_factory=set)


def context_tokens_bucket(usage: Usage) -> str:
    return _ai_usage_context_bucket(
        usage.diagnostics.get("context_tokens"),
        not usage.issues.intersection(("invalid_usage", "inconsistent_usage", "inconsistent_cache_ttl")),
    )


_DETAIL_FIELDS = (
    "text_tokens",
    "audio_tokens",
    "image_tokens",
    "video_tokens",
    "cached_tokens",
    "reasoning_tokens",
    "tool_use_tokens",
    "character_count",
    "image_count",
    "accepted_prediction_tokens",
    "rejected_prediction_tokens",
    "audio_length_seconds",
    "video_length_seconds",
)
_TOOL_FIELDS = (
    "web_search_requests",
    "tool_search_requests",
    "browser_open_requests",
    "google_maps_grounding_requests",
)


def _first(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def normalize_usage(raw: Any, operation: str = "completion") -> Usage:
    """Select LiteLLM fields; shared Rust code validates and partitions the counts.

    Never serialize the response: only known numeric usage fields cross the native
    boundary. No prompt, completion, arbitrary metadata, or credential is forwarded.
    """
    selected = None
    if raw is not None:
        prompt = get(raw, "prompt_tokens")
        output = get(raw, "completion_tokens")
        details = get(raw, "prompt_tokens_details") or get(raw, "input_tokens_details")
        completion = get(raw, "completion_tokens_details") or get(raw, "output_tokens_details")
        native = prompt is None and operation in ("responses", "aresponses", "anthropic_messages")
        if native:
            prompt, output = get(raw, "input_tokens"), get(raw, "output_tokens")
            details, completion = get(raw, "input_tokens_details"), get(raw, "output_tokens_details")
        ttl = get(details, "cache_creation_token_details") or get(raw, "cache_creation")
        selected = {
            "input": prompt,
            "output": output,
            "input_excludes_cache": native and operation == "anthropic_messages",
            "embedding": operation in ("embedding", "aembedding"),
            "cache_read": _first(get(details, "cached_tokens"), get(raw, "cache_read_input_tokens")),
            "cache_write": _first(
                get(details, "cache_creation_tokens"),
                get(details, "cache_write_tokens"),
                get(raw, "cache_creation_input_tokens"),
            ),
            "cache_write_5m": get(ttl, "ephemeral_5m_input_tokens"),
            "cache_write_1h": get(ttl, "ephemeral_1h_input_tokens"),
            "input_details": {key: get(details, key) for key in _DETAIL_FIELDS},
            "output_details": {key: get(completion, key) for key in _DETAIL_FIELDS},
        }
        tools = get(raw, "server_tool_use")
        for key in _TOOL_FIELDS:
            selected[key] = get(tools, key)
            selected["normalized_" + key] = get(details, key)
    quantities, diagnostics, issues = _normalize_ai_usage(selected)
    return Usage(quantities, diagnostics, set(issues))


@dataclass(frozen=True)
class UsageRecord:
    tags: dict[str, str]
    usage: Usage


class DatadogSink:
    """Send additive usage counters through the Agent's DogStatsD listener."""

    def __init__(self, client: Optional[DogStatsd] = None) -> None:
        self._client = client
        self._pid = os.getpid()

    def __call__(self, record: UsageRecord) -> None:
        if self._pid != os.getpid():
            # Do not reuse a parent's socket or potentially locked client locks.
            self._client = None
            self._pid = os.getpid()
        if self._client is None:
            # Delay configuration/socket setup until export so a bad endpoint never
            # prevents the gateway from starting. The callback handles export errors.
            self._client = get_dogstatsd_client(agent_config.dogstatsd_url)
        tags = [f"{key}:{value}" for key, value in sorted(record.tags.items())]
        # Use the vendored client directly: the shared metrics wrapper rounds
        # counters to integers, losing reported fractional audio/video seconds.
        for prefix, values in (("usage", record.usage.quantities), ("observed", record.usage.diagnostics)):
            for key, value in values.items():
                self._client.increment(f"ai_gateway.{prefix}.{key}", value, tags=tags)
        self._client.increment("ai_gateway.requests", tags=tags)

    def close(self) -> None:
        if self._client is not None and self._pid == os.getpid():
            self._client.close_socket()  # type: ignore[no-untyped-call]
        self._client = None
