from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TracerDebugInfo:
    """Tracer state consumed by ddtrace.internal.debug.collect()."""

    writer: Any
    sampling_rules: list[Any]
    tags: dict[str, Any]
    partial_flush_enabled: bool
    partial_flush_min_spans: int

    @classmethod
    def from_tracer(cls, tracer: Any) -> TracerDebugInfo:
        return cls(
            writer=tracer._span_aggregator.writer,
            sampling_rules=tracer._sampler.rules,
            tags=tracer._tags,
            partial_flush_enabled=tracer._span_aggregator.partial_flush_enabled,
            partial_flush_min_spans=tracer._span_aggregator.partial_flush_min_spans,
        )
