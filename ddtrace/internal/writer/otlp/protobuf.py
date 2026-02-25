"""
Serialize OTLP ExportTraceServiceRequest to protobuf bytes for HTTP/protobuf.

Uses opentelemetry-proto when available. Converts the same dict structure
produced by the mapper (used for JSON) into protobuf messages.
"""

from __future__ import annotations

from typing import Any


def _hex_to_bytes(hex_str: str, num_bytes: int) -> bytes:
    """Decode hex string to bytes, padding or truncating to num_bytes."""
    if not hex_str:
        return b"\x00" * num_bytes
    raw = bytes.fromhex(hex_str)
    if len(raw) < num_bytes:
        return raw.rjust(num_bytes, b"\x00")
    return raw[-num_bytes:] if len(raw) > num_bytes else raw


def _any_value_from_dict(val: dict[str, Any], any_pb2: Any) -> Any:
    """Build proto AnyValue from our _attribute_value dict."""
    if "stringValue" in val:
        a = any_pb2.AnyValue(string_value=val["stringValue"])
    elif "boolValue" in val:
        a = any_pb2.AnyValue(bool_value=val["boolValue"])
    elif "intValue" in val:
        a = any_pb2.AnyValue(int_value=int(val["intValue"]))
    elif "doubleValue" in val:
        a = any_pb2.AnyValue(double_value=val["doubleValue"])
    elif "arrayValue" in val:
        values = [_any_value_from_dict(v, any_pb2) for v in val["arrayValue"].get("values", [])]
        a = any_pb2.AnyValue(array_value=any_pb2.ArrayValue(values=values))
    else:
        a = any_pb2.AnyValue(string_value=str(val))
    return a


def _key_value_from_dict(kv: dict[str, Any], common_pb2: Any) -> Any:
    key = kv.get("key", "")
    value = kv.get("value", {})
    return common_pb2.KeyValue(key=key, value=_any_value_from_dict(value, common_pb2))


def _resource_from_dict(res: dict[str, Any], resource_pb2: Any, common_pb2: Any) -> Any:
    attrs = [_key_value_from_dict(a, common_pb2) for a in res.get("attributes", [])]
    return resource_pb2.Resource(attributes=attrs)


def _span_from_dict(s: dict[str, Any], trace_pb2: Any, common_pb2: Any) -> Any:
    trace_id = _hex_to_bytes(s.get("trace_id", ""), 16)
    span_id = _hex_to_bytes(s.get("span_id", ""), 8)
    parent_span_id = s.get("parent_span_id")
    if parent_span_id:
        parent_span_id = _hex_to_bytes(str(parent_span_id), 8)
    else:
        parent_span_id = b""
    start_ns = int(s.get("start_time_unix_nano", 0))
    end_ns = int(s.get("end_time_unix_nano", 0))
    kind = s.get("kind", 1)
    attrs = [_key_value_from_dict(a, common_pb2) for a in s.get("attributes", [])]
    dropped_attrs = s.get("dropped_attributes_count", 0)
    events = []
    for ev in s.get("events") or []:
        ev_pb = trace_pb2.Span.Event(
            time_unix_nano=int(ev.get("time_unix_nano", 0)),
            name=ev.get("name", ""),
            attributes=[_key_value_from_dict(a, common_pb2) for a in ev.get("attributes", [])],
        )
        events.append(ev_pb)
    dropped_events = s.get("dropped_events_count", 0)
    status_code = s.get("status", {}).get("code", 1)
    status_pb = trace_pb2.Status(code=status_code)
    span_pb = trace_pb2.Span(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        name=s.get("name", "span"),
        kind=kind,
        start_time_unix_nano=start_ns,
        end_time_unix_nano=end_ns,
        attributes=attrs,
        dropped_attributes_count=dropped_attrs,
        events=events,
        dropped_events_count=dropped_events,
        status=status_pb,
    )
    return span_pb


def _scope_spans_from_dict(ss: dict[str, Any], trace_pb2: Any, common_pb2: Any) -> Any:
    scope = ss.get("scope", {})
    scope_pb = common_pb2.InstrumentationScope(
        name=scope.get("name", ""),
        version=scope.get("version", ""),
    )
    spans = [_span_from_dict(s, trace_pb2, common_pb2) for s in ss.get("spans", [])]
    return trace_pb2.ScopeSpans(scope=scope_pb, spans=spans)


def _resource_spans_from_dict(rs: dict[str, Any], trace_pb2: Any, resource_pb2: Any, common_pb2: Any) -> Any:
    resource_pb = _resource_from_dict(rs.get("resource", {}), resource_pb2, common_pb2)
    scope_spans = [_scope_spans_from_dict(ss, trace_pb2, common_pb2) for ss in rs.get("scope_spans", [])]
    return trace_pb2.ResourceSpans(resource=resource_pb, scope_spans=scope_spans)


def otlp_request_to_protobuf_bytes(request: dict[str, Any]) -> bytes:
    """
    Serialize ExportTraceServiceRequest dict to protobuf bytes.

    :param request: Dict with resource_spans (from dd_trace_to_otlp_request).
    :returns: Serialized protobuf bytes for HTTP body (Content-Type: application/x-protobuf).
    :raises ImportError: If opentelemetry-proto is not installed.
    """
    from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
    from opentelemetry.proto.common.v1 import common_pb2
    from opentelemetry.proto.resource.v1 import resource_pb2
    from opentelemetry.proto.trace.v1 import trace_pb2

    resource_spans = [
        _resource_spans_from_dict(rs, trace_pb2, resource_pb2, common_pb2)
        for rs in request.get("resource_spans", [])
    ]
    msg = trace_service_pb2.ExportTraceServiceRequest(resource_spans=resource_spans)
    return msg.SerializeToString()


def otlp_protobuf_available() -> bool:
    """Return True if opentelemetry-proto is available for protobuf encoding."""
    try:
        from opentelemetry.proto.collector.trace.v1 import trace_service_pb2  # noqa: F401

        return True
    except ImportError:
        return False
