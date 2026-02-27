from __future__ import annotations

import json
from typing import Any


def _snake_to_camel(key: str) -> str:
    """Convert snake_case to lowerCamelCase. Keys with no underscore are returned as-is."""
    if "_" not in key:
        return key
    parts = key.split("_")
    return parts[0].lower() + "".join(p.capitalize() for p in parts[1:])


def _to_otlp_json_structure(obj: Any) -> Any:
    """
    Drop keys with None values and convert dict keys to lowerCamelCase for OTLP JSON.
    """
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {_snake_to_camel(k): _to_otlp_json_structure(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_to_otlp_json_structure(x) for x in obj]
    return obj


def otlp_request_to_json_bytes(request: dict[str, Any]) -> bytes:
    """
    Serialize ExportTraceServiceRequest to UTF-8 JSON bytes.

    OTLP JSON encoding: object keys in lowerCamelCase (per OpenTelemetry
    protocol specification). Attribute keys inside KeyValue remain as-is.

    :param request: Dict with resource_spans (from dd_trace_to_otlp_request).
    :returns: UTF-8 encoded JSON bytes for HTTP body.
    """
    prepared = _to_otlp_json_structure(request)
    return json.dumps(prepared, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
