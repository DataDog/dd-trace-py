from __future__ import annotations

import json
from typing import Any


def _snake_to_camel(key: str) -> str:
    """Convert snake_case to lowerCamelCase."""
    if "_" not in key:
        return key
    parts = key.split("_")
    return parts[0].lower() + "".join(p.capitalize() for p in parts[1:])


def _clean_nulls(obj: Any) -> Any:
    """Remove keys with None values for cleaner JSON (optional)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {k: _clean_nulls(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_clean_nulls(x) for x in obj]
    return obj


def _keys_to_camel(obj: Any) -> Any:
    """Recursively convert dict keys to lowerCamelCase for OTLP JSON spec."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {_snake_to_camel(k): _keys_to_camel(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_keys_to_camel(x) for x in obj]
    return obj


def otlp_request_to_json_bytes(request: dict[str, Any]) -> bytes:
    """
    Serialize ExportTraceServiceRequest to UTF-8 JSON bytes.

    OTLP JSON encoding: object keys in lowerCamelCase (per OpenTelemetry
    protocol specification). Attribute keys inside KeyValue remain as-is.

    :param request: Dict with resource_spans (from dd_trace_to_otlp_request).
    :returns: UTF-8 encoded JSON bytes for HTTP body.
    """
    cleaned = _clean_nulls(request)
    camel = _keys_to_camel(cleaned)
    return json.dumps(camel, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
