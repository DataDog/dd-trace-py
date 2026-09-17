"""Content-free usage records and conservative LiteLLM usage normalization."""

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
import math
from typing import Any
from typing import Optional

from ddtrace import tracer as default_tracer
from ddtrace.trace import Context
from ddtrace.trace import Tracer


def get(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


def label(value: Any) -> Optional[str]:
    # Never stringify containers, exceptions, arbitrary objects, or secret-looking values.
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        return None
    if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    if value.lower().startswith(("sk-", "bearer ")):
        return None
    return value


@dataclass(frozen=True)
class BillingScope:
    """Operator-maintained scope for ONE selected deployment/credential, not a model alias.

    Values must be non-secret IDs. Absent dimensions stay absent. Mode and geography
    are billed mode/geography, not the gateway's region or a requested tier.
    """

    provider: str
    account_id: str
    product: str
    project_id: Optional[str] = None
    resource_id: Optional[str] = None
    api_key_id: Optional[str] = None
    geography: Optional[str] = None
    mode: Optional[str] = None
    model: Optional[str] = None

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if value is not None and label(value) is None:
                raise ValueError(f"Invalid non-secret billing field: {name}")
        if not all((self.provider, self.account_id, self.product)):
            raise ValueError("Billing provider, account_id and product are required")

    def tags(self) -> dict[str, str]:
        return {f"ai.billing.{key}": value for key, value in vars(self).items() if value is not None and key != "model"}


@dataclass
class Usage:
    quantities: dict[str, int] = field(default_factory=dict)
    diagnostics: dict[str, float] = field(default_factory=dict)
    issues: set[str] = field(default_factory=set)


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
            cached = count(raw, "cache_read_input_tokens", 0) or 0
        written = count(details, "cache_creation_tokens")
        if written is None:
            written = count(details, "cache_write_tokens")
        if written is None:
            written = count(raw, "cache_creation_input_tokens", 0) or 0
        audio_out = count(completion, "audio_tokens", 0) or 0
        reasoning = count(completion, "reasoning_tokens", 0) or 0
        result.diagnostics.update(input_cache_read_tokens=cached, input_cache_write_tokens=written)
        ttl = get(details, "cache_creation_token_details") or get(raw, "cache_creation")
        five = count(ttl, "ephemeral_5m_input_tokens", 0) or 0
        hour = count(ttl, "ephemeral_1h_input_tokens", 0) or 0
        if five + hour > written:
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
        result.quantities.update(
            input_uncached_tokens=prompt - cached - written,
            input_cache_read_tokens=cached,
        )
        if output is not None:
            result.quantities["output_tokens"] = output
        result.diagnostics["reasoning_output_tokens"] = reasoning  # subset of output, not additive
        result.quantities.update(
            input_cache_write_5m_tokens=five,
            input_cache_write_1h_tokens=hour,
            input_cache_write_unknown_ttl_tokens=written - five - hour,
        )
        if written - five - hour:
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
    start: float
    end: float
    tags: dict[str, str]
    usage: Usage
    parent: Optional[Context] = None
    error: bool = False


class DatadogSink:
    """Emit one content-free span, retaining the gateway request's APM parent."""

    def __init__(self, tracer: Tracer = default_tracer) -> None:
        self.tracer = tracer

    def __call__(self, record: UsageRecord) -> None:
        span = self.tracer.start_span(
            "ai_gateway.usage", child_of=record.parent, resource="ai_gateway.usage", activate=False
        )
        span.start = record.start
        span.error = int(record.error)
        try:
            span.set_tags(record.tags)
            for key, value in record.usage.quantities.items():
                span._set_attribute(f"ai.usage.{key}", value)
            for key, observation in record.usage.diagnostics.items():
                span._set_attribute(f"ai.observed.{key}", observation)
        finally:
            span.finish(finish_time=max(record.start, record.end))
