import json
import struct

from ddtrace.internal.otlp_stats.aggregation import SpanAggKey
from ddtrace.internal.otlp_stats.aggregation import SpanAggStats
from ddtrace.internal.otlp_stats.aggregation import SpanBuckets
from ddtrace.internal.otlp_stats import serializer
from ddtrace.trace import Span


BUCKET_SIZE_NS = 10 * 1_000_000_000
TIME_NS = 12340000000000
RESOURCE_ATTRS = {"service.name": "test-service", "deployment.environment": "test"}
VERSION = "1.2.3"


def _span(name="test.op", resource="GET /foo", span_type="web", status="200", method=None, route=None, origin=None):
    s = Span(name, service="svc", resource=resource, span_type=span_type)
    if status is not None:
        s.set_tag("http.status_code", status)
    if method is not None:
        s.set_tag("http.method", method)
    if route is not None:
        s.set_tag("http.route", route)
    if origin is not None:
        s.set_tag("_dd.origin", origin)
    return s


def _stats(**counters):
    stats = SpanAggStats(SpanAggKey(_span()))
    for k, v in counters.items():
        setattr(stats, k, v)
    return stats


def _drained(stats):
    return [(TIME_NS, {stats.agg_key: stats})]


# --- protobuf decoding helpers (test-only) ---


def _read_varint(buf, i):
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _parse_pb(buf):
    fields = {}
    i, n = 0, len(buf)
    while i < n:
        key, i = _read_varint(buf, i)
        field, wt = key >> 3, key & 7
        if wt == 0:
            val, i = _read_varint(buf, i)
        elif wt == 1:
            val, i = buf[i : i + 8], i + 8
        elif wt == 2:
            ln, i = _read_varint(buf, i)
            val, i = buf[i : i + ln], i + ln
        else:
            raise ValueError("unsupported wire type %d" % wt)
        fields.setdefault(field, []).append(val)
    return fields


def _pb_metric(buf):
    request = _parse_pb(buf)
    rm = _parse_pb(request[1][0])
    sm = _parse_pb(rm[2][0])
    return _parse_pb(sm[2][0]), rm


def _pb_data_points(buf):
    metric, _ = _pb_metric(buf)
    histogram = _parse_pb(metric[9][0])
    points = []
    for dp_bytes in histogram.get(1, []):
        dp = _parse_pb(dp_bytes)
        attrs = {}
        for kv_bytes in dp.get(9, []):
            kv = _parse_pb(kv_bytes)
            key = kv[1][0].decode("utf-8")
            any_value = _parse_pb(kv[2][0])
            attrs[key] = any_value[1][0].decode("utf-8") if 1 in any_value else bool(any_value[2][0])
        points.append(
            {
                "start": struct.unpack("<Q", dp[2][0])[0],
                "end": struct.unpack("<Q", dp[3][0])[0],
                "count": struct.unpack("<Q", dp[4][0])[0],
                "sum": struct.unpack("<d", dp[5][0])[0],
                "attrs": attrs,
            }
        )
    return points, histogram


# --- aggregation ---


def test_aggregation_counts_and_durations():
    bucket = SpanBuckets()
    ok = _span()
    ok.duration_ns = 1_000_000_000
    tl = _span()
    tl._set_attribute("_dd.top_level", 1)
    tl.duration_ns = 2_000_000_000
    err_tl = _span()
    err_tl._set_attribute("_dd.top_level", 1)
    err_tl.error = 1
    err_tl.duration_ns = 3_000_000_000
    for s in (ok, tl, err_tl):
        bucket.for_span(s).record(s)

    stats = next(iter(bucket.values()))
    assert stats.hits == 3
    assert stats.errors == 1
    assert stats.top_level_hits == 2
    assert stats.top_level_errors == 1
    assert stats.duration == 6_000_000_000
    assert stats.error_duration == 3_000_000_000
    assert stats.top_level_duration == 5_000_000_000
    assert stats.top_level_error_duration == 3_000_000_000


def test_aggregation_buckets_by_dimension():
    bucket = SpanBuckets()
    bucket.for_span(_span(resource="GET /a")).record(_span(resource="GET /a"))
    bucket.for_span(_span(resource="GET /b")).record(_span(resource="GET /b"))
    assert len(bucket) == 2


# --- JSON serialization ---


def test_json_single_duration_metric():
    payload = json.loads(serializer.to_json(_drained(_stats(hits=1)), BUCKET_SIZE_NS, RESOURCE_ATTRS, VERSION))
    metrics = payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
    assert len(metrics) == 1
    assert metrics[0]["name"] == "dd.trace.span.duration"
    assert metrics[0]["unit"] == "s"
    assert metrics[0]["histogram"]["aggregationTemporality"] == "AGGREGATION_TEMPORALITY_DELTA"


def test_json_scope_and_resource():
    payload = json.loads(serializer.to_json(_drained(_stats(hits=1)), BUCKET_SIZE_NS, RESOURCE_ATTRS, VERSION))
    rm = payload["resourceMetrics"][0]
    assert rm["scopeMetrics"][0]["scope"] == {"name": "dd-trace", "version": VERSION}
    attrs = {a["key"]: a["value"]["stringValue"] for a in rm["resource"]["attributes"]}
    assert attrs["service.name"] == "test-service"
    assert attrs["deployment.environment"] == "test"


def test_json_dimension_mapping():
    stats = SpanAggStats(SpanAggKey(_span(status="404", method="POST", route="/users/:id")))
    stats.hits = 1
    dp = json.loads(serializer.to_json(_drained(stats), BUCKET_SIZE_NS, RESOURCE_ATTRS, VERSION))[
        "resourceMetrics"
    ][0]["scopeMetrics"][0]["metrics"][0]["histogram"]["dataPoints"][0]
    attrs = {a["key"]: a["value"]["stringValue"] for a in dp["attributes"]}
    assert attrs["span.name"] == "GET /foo"
    assert attrs["dd.operation.name"] == "test.op"
    assert attrs["dd.span.type"] == "web"
    assert attrs["http.response.status_code"] == "404"
    assert attrs["http.request.method"] == "POST"
    assert attrs["http.route"] == "/users/:id"


def test_json_omits_absent_http_attrs():
    stats = SpanAggStats(SpanAggKey(_span(status=None)))
    stats.hits = 1
    dp = json.loads(serializer.to_json(_drained(stats), BUCKET_SIZE_NS, RESOURCE_ATTRS, VERSION))[
        "resourceMetrics"
    ][0]["scopeMetrics"][0]["metrics"][0]["histogram"]["dataPoints"][0]
    keys = {a["key"] for a in dp["attributes"]}
    assert "http.response.status_code" not in keys
    assert "http.request.method" not in keys
    assert "http.route" not in keys


def test_json_ns_to_seconds_and_timestamps():
    dp = json.loads(
        serializer.to_json(_drained(_stats(hits=1, duration=2_000_000_000)), BUCKET_SIZE_NS, RESOURCE_ATTRS, VERSION)
    )["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["histogram"]["dataPoints"][0]
    assert dp["sum"] == 2.0
    assert dp["startTimeUnixNano"] == str(TIME_NS)
    assert dp["timeUnixNano"] == str(TIME_NS + BUCKET_SIZE_NS)


def test_json_matrix_four_cells():
    # 2 ok-not-top-level, 2 ok-top-level, 1 err-not-top-level, 1 err-top-level
    stats = _stats(
        hits=6,
        errors=2,
        top_level_hits=3,
        top_level_errors=1,
        duration=6_000_000_000,
        error_duration=2_000_000_000,
        top_level_duration=3_000_000_000,
        top_level_error_duration=1_000_000_000,
    )
    points = json.loads(serializer.to_json(_drained(stats), BUCKET_SIZE_NS, RESOURCE_ATTRS, VERSION))[
        "resourceMetrics"
    ][0]["scopeMetrics"][0]["metrics"][0]["histogram"]["dataPoints"]
    assert len(points) == 4

    def count(error, top_level):
        for p in points:
            a = {x["key"]: x["value"]["stringValue"] for x in p["attributes"]}
            has_err = a.get("error") == "true"
            if has_err == error and a["dd.top_level"] == ("true" if top_level else "false"):
                return int(p["count"])
        return 0

    assert count(False, False) == 2
    assert count(False, True) == 2
    assert count(True, False) == 1
    assert count(True, True) == 1


def test_json_omits_zero_count_cells():
    # only top-level errors => single data point
    stats = _stats(hits=1, errors=1, top_level_hits=1, top_level_errors=1)
    points = json.loads(serializer.to_json(_drained(stats), BUCKET_SIZE_NS, RESOURCE_ATTRS, VERSION))[
        "resourceMetrics"
    ][0]["scopeMetrics"][0]["metrics"][0]["histogram"]["dataPoints"]
    assert len(points) == 1
    attrs = {a["key"]: a["value"]["stringValue"] for a in points[0]["attributes"]}
    assert attrs["error"] == "true"
    assert attrs["dd.top_level"] == "true"


def test_json_no_error_attr_on_ok_points():
    points = json.loads(serializer.to_json(_drained(_stats(hits=1)), BUCKET_SIZE_NS, RESOURCE_ATTRS, VERSION))[
        "resourceMetrics"
    ][0]["scopeMetrics"][0]["metrics"][0]["histogram"]["dataPoints"]
    assert all(a["key"] != "error" for a in points[0]["attributes"])


def test_json_synthetics_attr():
    stats = SpanAggStats(SpanAggKey(_span(origin="synthetics")))
    stats.hits = 1
    dp = json.loads(serializer.to_json(_drained(stats), BUCKET_SIZE_NS, RESOURCE_ATTRS, VERSION))[
        "resourceMetrics"
    ][0]["scopeMetrics"][0]["metrics"][0]["histogram"]["dataPoints"][0]
    attrs = {a["key"]: a["value"]["stringValue"] for a in dp["attributes"]}
    assert attrs["dd.synthetics"] == "true"


# --- protobuf serialization ---


def test_protobuf_single_duration_metric():
    buf = serializer.to_protobuf(_drained(_stats(hits=1)), BUCKET_SIZE_NS, RESOURCE_ATTRS, VERSION)
    metric, _ = _pb_metric(buf)
    assert metric[1][0].decode("utf-8") == "dd.trace.span.duration"
    assert metric[3][0].decode("utf-8") == "s"


def test_protobuf_delta_temporality_and_values():
    buf = serializer.to_protobuf(
        _drained(_stats(hits=1, duration=2_000_000_000)), BUCKET_SIZE_NS, RESOURCE_ATTRS, VERSION
    )
    points, histogram = _pb_data_points(buf)
    assert histogram[2][0] == serializer.DELTA_TEMPORALITY
    assert len(points) == 1
    assert points[0]["count"] == 1
    assert points[0]["sum"] == 2.0
    assert points[0]["start"] == TIME_NS
    assert points[0]["end"] == TIME_NS + BUCKET_SIZE_NS


def test_protobuf_bool_attrs():
    stats = _stats(hits=1, errors=1, top_level_hits=1, top_level_errors=1)
    points, _ = _pb_data_points(serializer.to_protobuf(_drained(stats), BUCKET_SIZE_NS, RESOURCE_ATTRS, VERSION))
    assert points[0]["attrs"]["error"] is True
    assert points[0]["attrs"]["dd.top_level"] is True


def test_protobuf_matrix_four_cells():
    stats = _stats(
        hits=6,
        errors=2,
        top_level_hits=3,
        top_level_errors=1,
        duration=6_000_000_000,
        error_duration=2_000_000_000,
        top_level_duration=3_000_000_000,
        top_level_error_duration=1_000_000_000,
    )
    points, _ = _pb_data_points(serializer.to_protobuf(_drained(stats), BUCKET_SIZE_NS, RESOURCE_ATTRS, VERSION))
    assert len(points) == 4
