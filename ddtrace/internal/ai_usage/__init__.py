"""LLM usage and cost metrics through libdd-ai-usage, shared by the integrations that record them.

The native module reads provider responses and streams by the library's shared rules, projects observations into
metric points and encodes them; UsageMetricsWriter in _writer exports them. Integrations only collect fields.
"""

from __future__ import annotations

from typing import Any
from typing import Optional


try:
    from ddtrace.internal.native import ai_usage
except ImportError:
    # Builds without the native ai_usage module (serverless) cannot record these metrics.
    ai_usage = None  # type: ignore[misc,assignment]


__all__ = ["ai_usage", "parse", "parse_stream"]


def parse(usage_format: str, payload: Any, embeddings: bool = False) -> tuple[dict[str, Any], str, Optional[str]]:
    """What a provider response reported: (response, outcome, error_name). payload is a decoded response or event (a
    dict of JSON values, or a list of events), or a whole body as str or bytes. response holds usage (None when the
    provider reported none), response_model and service_tier.
    """
    return ai_usage.parse(usage_format, payload, embeddings)


def parse_stream(usage_format: str, chunks: Any, embeddings: bool = False) -> tuple[dict[str, Any], str, Optional[str]]:
    """What a stream reported, from its raw lines or events in order, as parse returns it. outcome is completed when
    the provider ended the response, failed when it reported an error, else open.
    """
    return ai_usage.parse_stream(usage_format, chunks, embeddings)
