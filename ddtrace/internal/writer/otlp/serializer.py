"""
Serialize OTLP ExportTraceServiceRequest to UTF-8 JSON.

Matches OTLP JSON encoding: snake_case field names, nano timestamps as strings.
"""

from __future__ import annotations

import json
from typing import Any


def _clean_nulls(obj: Any) -> Any:
    """Remove keys with None values for cleaner JSON (optional)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {k: _clean_nulls(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_clean_nulls(x) for x in obj]
    return obj


def otlp_request_to_json_bytes(request: dict[str, Any]) -> bytes:
    """
    Serialize ExportTraceServiceRequest to UTF-8 JSON bytes.

    :param request: Dict with resource_spans (from dd_trace_to_otlp_request).
    :returns: UTF-8 encoded JSON bytes for HTTP body.
    """
    cleaned = _clean_nulls(request)
    return json.dumps(cleaned, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
