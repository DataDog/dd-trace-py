"""Content-free usage records and conservative LiteLLM usage normalization."""

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
import math
import os
from typing import Any
from typing import Optional

from ddtrace.internal.dogstatsd import get_dogstatsd_client
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.vendor.dogstatsd import DogStatsd


def get(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


def label(value: Any, max_length: int = 256) -> Optional[str]:
    # Never stringify containers, exceptions, arbitrary objects, or secret-looking values.
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        return None
    if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    if value.lower().startswith(("sk-", "bearer ")):
        return None
    return value


@dataclass
class Usage:
    quantities: dict[str, int] = field(default_factory=dict)
    diagnostics: dict[str, float] = field(default_factory=dict)
    issues: set[str] = field(default_factory=set)


# Additional boundaries between the generic 32k, 64k, 128k, ... ranges:
# https://ai.google.dev/gemini-api/docs/pricing (200k)
# https://developers.openai.com/api/docs/models/gpt-5.4-pro (272k)
_CONTEXT_TOKEN_EXTRA_BOUNDARIES = (200_000, 272_000)


def context_tokens_bucket(usage: Usage) -> str:
    tokens = usage.diagnostics.get("context_tokens")
    if (
        tokens is None
        or type(tokens) is not int
        or not 0 <= tokens <= 2**53
        or usage.issues.intersection(("invalid_usage", "inconsistent_usage", "inconsistent_cache_ttl"))
    ):
        return "unknown"
    lower = 0
    upper = 32_000
    while tokens > upper:
        lower = upper + 1
        upper *= 2
    for boundary in _CONTEXT_TOKEN_EXTRA_BOUNDARIES:
        if tokens <= boundary:
            upper = min(upper, boundary)
        else:
            lower = max(lower, boundary + 1)
    return f"{lower}_{upper}"


class _UsageError(Exception):
    pass


def normalize_usage(raw: Any, operation: str = "completion") -> Usage:
    """Partition LiteLLM's inclusive prompt counts; never add cache/reasoning twice.

    Categories carry their unit in the metric name. Unknown cache-write TTL stays
    unknown, not 5m. Ambiguous multimodal input retains totals but is not partitioned.
    No tokenizer, price table, or inferred zero replaces missing response usage.
    """
    result = Usage()
    if raw is None:
        result.issues.add("missing_usage")
        return result

    def count(obj: Any, key: str, default: Optional[int] = None) -> Optional[int]:
        value = get(obj, key, default)
        if value is None:
            return None
        if type(value) is not int or value < 0 or value > 2**53:
            raise _UsageError("invalid_usage")
        return value

    try:
        prompt = count(raw, "prompt_tokens")
        output = count(raw, "completion_tokens")
        details = get(raw, "prompt_tokens_details") or get(raw, "input_tokens_details")
        completion = get(raw, "completion_tokens_details") or get(raw, "output_tokens_details")
        # Preserve explicitly reported modality/unit counters even when their overlap
        # with caching prevents a safe universal disjoint partition.
        for prefix, obj in (("input", details), ("output", completion)):
            for name in (
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
            ):
                value = count(obj, name)
                if value is not None:
                    result.diagnostics[f"{prefix}_{name}"] = value
            for name in ("audio_length_seconds", "video_length_seconds"):
                duration = get(obj, name)
                if duration is not None:
                    if type(duration) not in (int, float) or not 0 <= duration <= 2**53 or not math.isfinite(duration):
                        raise _UsageError("invalid_usage")
                    result.diagnostics[f"{prefix}_{name}"] = duration
        for name in (
            "web_search_requests",
            "tool_search_requests",
            "browser_open_requests",
            "google_maps_grounding_requests",
        ):
            native = count(get(raw, "server_tool_use"), name)
            normalized = count(details, name)
            if native is not None and normalized is not None and native != normalized:
                result.issues.add("conflicting_tool_usage")
            value = native if native is not None else normalized
            if value is not None:
                result.diagnostics[name] = value
        if prompt is None and operation in ("responses", "aresponses", "anthropic_messages"):
            prompt = count(raw, "input_tokens")
            output = count(raw, "output_tokens")
            details = get(raw, "input_tokens_details")
            completion = get(raw, "output_tokens_details")
            if prompt is not None and operation == "anthropic_messages":
                # Native Anthropic input excludes both caches, unlike LiteLLM's normalized input.
                prompt += count(raw, "cache_read_input_tokens", 0) or 0
                prompt += count(raw, "cache_creation_input_tokens", 0) or 0
        embedding = operation in ("embedding", "aembedding")
        if prompt is None or (output is None and not embedding):
            result.issues.add("unsupported_usage_shape")
            return result
        if prompt > 2**53:
            raise _UsageError("invalid_usage")
        result.diagnostics.update(input_tokens=prompt, context_tokens=prompt)
        if output is not None:
            result.diagnostics["output_tokens"] = output
        cached = count(details, "cached_tokens")
        if cached is None:
            cached = count(raw, "cache_read_input_tokens")
        written = count(details, "cache_creation_tokens")
        if written is None:
            written = count(details, "cache_write_tokens")
        if written is None:
            written = count(raw, "cache_creation_input_tokens")
        result.diagnostics["input_cache_read_reported"] = int(cached is not None)
        result.diagnostics["input_cache_write_reported"] = int(written is not None)
        for name, value in (("read", cached), ("write", written)):
            if value is not None:
                result.diagnostics[f"input_cache_{name}_tokens"] = value
            elif not embedding:
                result.issues.add(f"cache_{name}_detail_missing")
        cache_complete = embedding or (cached is not None and written is not None)
        cached = cached or 0
        written = written or 0
        audio_out = count(completion, "audio_tokens", 0) or 0
        reasoning = count(completion, "reasoning_tokens", 0) or 0
        ttl = get(details, "cache_creation_token_details") or get(raw, "cache_creation")
        five = count(ttl, "ephemeral_5m_input_tokens", 0) or 0
        hour = count(ttl, "ephemeral_1h_input_tokens", 0) or 0
        if result.diagnostics["input_cache_write_reported"] and five + hour > written:
            raise _UsageError("inconsistent_cache_ttl")
        result.diagnostics.update(input_cache_write_5m_tokens=five, input_cache_write_1h_tokens=hour)
        if cached + written > prompt or audio_out > (output or 0) or reasoning > (output or 0):
            raise _UsageError("inconsistent_usage")
        if any(
            result.diagnostics.get(f"{prefix}_{key}", 0)
            for prefix in ("input", "output")
            for key in (
                "audio_tokens",
                "image_tokens",
                "video_tokens",
                "image_count",
                "audio_length_seconds",
                "video_length_seconds",
            )
        ):
            # Cached audio/image can overlap cached_tokens; no universal partition exists.
            result.issues.add("multimodal_partition_unsupported")
            return result
        if cache_complete:
            result.quantities["input_uncached_tokens"] = prompt - cached - written
        if result.diagnostics["input_cache_read_reported"]:
            result.quantities["input_cache_read_tokens"] = cached
        if output is not None:
            result.quantities["output_tokens"] = output
        result.diagnostics["reasoning_output_tokens"] = reasoning  # subset of output, not additive
        if result.diagnostics["input_cache_write_reported"]:
            result.quantities.update(
                input_cache_write_5m_tokens=five,
                input_cache_write_1h_tokens=hour,
                input_cache_write_unknown_ttl_tokens=written - five - hour,
            )
        if written > five + hour:
            result.issues.add("cache_write_ttl_unknown")
        for name in (
            "web_search_requests",
            "tool_search_requests",
            "browser_open_requests",
            "google_maps_grounding_requests",
        ):
            if name in result.diagnostics and "conflicting_tool_usage" not in result.issues:
                result.quantities[name] = int(result.diagnostics[name])
    except _UsageError as exc:
        result.quantities.clear()
        result.issues.add(str(exc))
    return result


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
