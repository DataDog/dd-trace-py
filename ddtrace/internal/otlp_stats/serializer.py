"""Lightweight OTLP metrics serializer for client-computed span stats.

Emits a single ``dd.trace.span.duration`` histogram (count + sum only, delta temporality)
as an OTLP ``ExportMetricsServiceRequest`` in protobuf or JSON, without depending on the
OpenTelemetry SDK. See the RFC "OTLP Trace Metrics Export" for the data model.
"""
import json
import struct
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from typing import Any
    from typing import Iterator
    from typing import Mapping
    from typing import Optional
    from typing import Union

    from ddtrace.internal.otlp_stats.aggregation import SpanAggKey
    from ddtrace.internal.otlp_stats.aggregation import SpanAggStats

    _AttrValue = Union[str, bool]
    _Attrs = list[tuple[str, _AttrValue]]
    _DataPoint = tuple[_Attrs, int, int, int, float]
    _Drained = list[tuple[int, "Mapping[Any, SpanAggStats]"]]


SCOPE_NAME = "dd-trace"
METRIC_NAME = "dd.trace.span.duration"
METRIC_UNIT = "s"
DELTA_TEMPORALITY = 1
_DELTA_TEMPORALITY_NAME = "AGGREGATION_TEMPORALITY_DELTA"
NS_PER_S = 1e9

CONTENT_TYPE_PROTOBUF = "application/x-protobuf"
CONTENT_TYPE_JSON = "application/json"


def serialize(
    drained: "_Drained",
    bucket_size_ns: int,
    resource_attrs: "Mapping[str, str]",
    version: str,
    protocol: str,
) -> "tuple[bytes, str]":
    """Serialize drained buckets to an OTLP payload and its Content-Type."""
    if protocol == "http/json":
        return to_json(drained, bucket_size_ns, resource_attrs, version), CONTENT_TYPE_JSON
    return to_protobuf(drained, bucket_size_ns, resource_attrs, version), CONTENT_TYPE_PROTOBUF


def _base_attrs(agg_key: "SpanAggKey") -> "_Attrs":
    attrs: "_Attrs" = [
        ("span.name", agg_key.resource),
        ("dd.operation.name", agg_key.name),
        ("dd.span.type", agg_key.type),
        ("dd.synthetics", bool(agg_key.synthetics)),
    ]
    if agg_key.status_code:
        attrs.append(("http.response.status_code", str(agg_key.status_code)))
    if agg_key.method:
        attrs.append(("http.request.method", agg_key.method))
    if agg_key.endpoint:
        attrs.append(("http.route", agg_key.endpoint))
    return attrs


def _iter_data_points(drained: "_Drained", bucket_size_ns: int) -> "Iterator[_DataPoint]":
    """Yield the (ok/error) x (not-top-level/top-level) matrix of non-empty data points."""
    for time_ns, buckets in drained:
        end_ns = time_ns + bucket_size_ns
        for stats in buckets.values():
            base = _base_attrs(stats.agg_key)
            ok_not_tl = stats.hits - stats.errors - (stats.top_level_hits - stats.top_level_errors)
            ok_tl = stats.top_level_hits - stats.top_level_errors
            err_not_tl = stats.errors - stats.top_level_errors
            err_tl = stats.top_level_errors

            ok_not_tl_dur = (stats.duration - stats.error_duration) - (
                stats.top_level_duration - stats.top_level_error_duration
            )
            ok_tl_dur = stats.top_level_duration - stats.top_level_error_duration
            err_not_tl_dur = stats.error_duration - stats.top_level_error_duration
            err_tl_dur = stats.top_level_error_duration

            if ok_not_tl > 0:
                yield base + [("dd.top_level", False)], time_ns, end_ns, ok_not_tl, ok_not_tl_dur / NS_PER_S
            if ok_tl > 0:
                yield base + [("dd.top_level", True)], time_ns, end_ns, ok_tl, ok_tl_dur / NS_PER_S
            if err_not_tl > 0:
                yield base + [("error", True), ("dd.top_level", False)], (
                    time_ns
                ), end_ns, err_not_tl, err_not_tl_dur / NS_PER_S
            if err_tl > 0:
                yield base + [("error", True), ("dd.top_level", True)], (
                    time_ns
                ), end_ns, err_tl, err_tl_dur / NS_PER_S


# --- JSON (OTLP/JSON: 64-bit ints as strings, all attribute values as stringValue) ---


def _json_attr(key: str, value: "_AttrValue") -> "dict[str, Any]":
    sval = ("true" if value else "false") if isinstance(value, bool) else value
    return {"key": key, "value": {"stringValue": sval}}


def to_json(
    drained: "_Drained", bucket_size_ns: int, resource_attrs: "Mapping[str, str]", version: str
) -> bytes:
    data_points = [
        {
            "attributes": [_json_attr(k, v) for k, v in attrs],
            "startTimeUnixNano": str(start_ns),
            "timeUnixNano": str(end_ns),
            "count": str(count),
            "sum": sum_s,
            "bucketCounts": [],
            "explicitBounds": [],
        }
        for attrs, start_ns, end_ns, count, sum_s in _iter_data_points(drained, bucket_size_ns)
    ]
    payload = {
        "resourceMetrics": [
            {
                "resource": {"attributes": [_json_attr(k, v) for k, v in resource_attrs.items()]},
                "scopeMetrics": [
                    {
                        "scope": {"name": SCOPE_NAME, "version": version},
                        "metrics": [
                            {
                                "name": METRIC_NAME,
                                "unit": METRIC_UNIT,
                                "histogram": {
                                    "dataPoints": data_points,
                                    "aggregationTemporality": _DELTA_TEMPORALITY_NAME,
                                },
                            }
                        ],
                    }
                ],
            }
        ]
    }
    return json.dumps(payload).encode("utf-8")


# --- protobuf (minimal wire-format encoder for the fixed OTLP metrics schema) ---


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _tag(field: int, wire_type: int) -> bytes:
    return _varint((field << 3) | wire_type)


def _len_delim(field: int, payload: bytes) -> bytes:
    return _tag(field, 2) + _varint(len(payload)) + payload


def _pb_string(field: int, value: str) -> bytes:
    return _len_delim(field, value.encode("utf-8"))


def _pb_fixed64(field: int, value: int) -> bytes:
    return _tag(field, 1) + struct.pack("<Q", value)


def _pb_double(field: int, value: float) -> bytes:
    return _tag(field, 1) + struct.pack("<d", value)


def _pb_varint(field: int, value: int) -> bytes:
    return _tag(field, 0) + _varint(value)


def _pb_kv(key: str, value: "_AttrValue") -> bytes:
    # KeyValue{key=1, value=2}; AnyValue{string_value=1, bool_value=2}
    any_value = (_pb_varint(2, 1 if value else 0)) if isinstance(value, bool) else _pb_string(1, value)
    return _pb_string(1, key) + _len_delim(2, any_value)


def to_protobuf(
    drained: "_Drained", bucket_size_ns: int, resource_attrs: "Mapping[str, str]", version: str
) -> bytes:
    dp_msgs = []
    for attrs, start_ns, end_ns, count, sum_s in _iter_data_points(drained, bucket_size_ns):
        # HistogramDataPoint{start=2, time=3, count=4, sum=5, attributes=9}
        dp = _pb_fixed64(2, start_ns) + _pb_fixed64(3, end_ns) + _pb_fixed64(4, count) + _pb_double(5, sum_s)
        for k, v in attrs:
            dp += _len_delim(9, _pb_kv(k, v))
        dp_msgs.append(dp)

    # Histogram{data_points=1, aggregation_temporality=2}
    histogram = b"".join(_len_delim(1, dp) for dp in dp_msgs) + _pb_varint(2, DELTA_TEMPORALITY)
    # Metric{name=1, unit=3, histogram=9}
    metric = _pb_string(1, METRIC_NAME) + _pb_string(3, METRIC_UNIT) + _len_delim(9, histogram)
    # InstrumentationScope{name=1, version=2}
    scope = _pb_string(1, SCOPE_NAME) + _pb_string(2, version)
    # ScopeMetrics{scope=1, metrics=2}
    scope_metrics = _len_delim(1, scope) + _len_delim(2, metric)
    # Resource{attributes=1}
    resource = b"".join(_len_delim(1, _pb_kv(k, v)) for k, v in resource_attrs.items())
    # ResourceMetrics{resource=1, scope_metrics=2}
    resource_metrics = _len_delim(1, resource) + _len_delim(2, scope_metrics)
    # ExportMetricsServiceRequest{resource_metrics=1}
    return _len_delim(1, resource_metrics)
