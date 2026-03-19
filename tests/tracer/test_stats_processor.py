# coding: utf-8
"""Unit tests for client-side stats aggregation dimensions.

Tests new dimensions added to match Go reference implementation:
- span_kind
- is_trace_root (Trilean)
- peer_tags (client/producer/consumer only)
- gRPC status code extraction
- span.kind-based eligibility
"""

from unittest import mock

from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.internal.processor.stats import _TRILEAN_FALSE
from ddtrace.internal.processor.stats import _TRILEAN_TRUE
from ddtrace.internal.processor.stats import DEFAULT_PEER_TAG_KEYS
from ddtrace.internal.processor.stats import SpanStatsProcessorV06
from ddtrace.internal.processor.stats import _get_grpc_status_code
from ddtrace.internal.processor.stats import _get_peer_tags
from ddtrace.internal.processor.stats import _has_eligible_span_kind
from ddtrace.internal.processor.stats import _span_aggr_key
from ddtrace.trace import Span


# ---------------------------------------------------------------------------
# _has_eligible_span_kind tests
# ---------------------------------------------------------------------------
class TestHasEligibleSpanKind:
    def test_server_span_eligible(self):
        span = Span("op")
        span.set_tag(SPAN_KIND, SpanKind.SERVER)
        assert _has_eligible_span_kind(span) is True

    def test_client_span_eligible(self):
        span = Span("op")
        span.set_tag(SPAN_KIND, SpanKind.CLIENT)
        assert _has_eligible_span_kind(span) is True

    def test_producer_span_eligible(self):
        span = Span("op")
        span.set_tag(SPAN_KIND, SpanKind.PRODUCER)
        assert _has_eligible_span_kind(span) is True

    def test_consumer_span_eligible(self):
        span = Span("op")
        span.set_tag(SPAN_KIND, SpanKind.CONSUMER)
        assert _has_eligible_span_kind(span) is True

    def test_internal_span_not_eligible(self):
        span = Span("op")
        span.set_tag(SPAN_KIND, SpanKind.INTERNAL)
        assert _has_eligible_span_kind(span) is False

    def test_no_span_kind_not_eligible(self):
        span = Span("op")
        assert _has_eligible_span_kind(span) is False

    def test_unknown_span_kind_not_eligible(self):
        span = Span("op")
        span.set_tag(SPAN_KIND, "unknown")
        assert _has_eligible_span_kind(span) is False


# ---------------------------------------------------------------------------
# _get_grpc_status_code tests
# ---------------------------------------------------------------------------
class TestGetGrpcStatusCode:
    def test_no_grpc_tags_returns_zero(self):
        span = Span("op")
        assert _get_grpc_status_code(span) == 0

    def test_rpc_grpc_status_code_first_priority(self):
        span = Span("op")
        span.set_tag("rpc.grpc.status_code", "2")
        span.set_tag("grpc.code", "5")
        assert _get_grpc_status_code(span) == 2

    def test_grpc_code_second_priority(self):
        span = Span("op")
        span.set_tag("grpc.code", "14")
        assert _get_grpc_status_code(span) == 14

    def test_rpc_grpc_status_dot_code_third_priority(self):
        span = Span("op")
        span.set_tag("rpc.grpc.status.code", "3")
        assert _get_grpc_status_code(span) == 3

    def test_grpc_status_code_fourth_priority(self):
        span = Span("op")
        span.set_tag("grpc.status.code", "7")
        assert _get_grpc_status_code(span) == 7

    def test_non_numeric_value_skipped(self):
        span = Span("op")
        span.set_tag("rpc.grpc.status_code", "NOT_FOUND")
        span.set_tag("grpc.code", "5")
        assert _get_grpc_status_code(span) == 5

    def test_all_non_numeric_returns_zero(self):
        span = Span("op")
        span.set_tag("rpc.grpc.status_code", "invalid")
        span.set_tag("grpc.code", "also_invalid")
        assert _get_grpc_status_code(span) == 0


# ---------------------------------------------------------------------------
# _get_peer_tags tests
# ---------------------------------------------------------------------------
class TestGetPeerTags:
    def test_client_span_extracts_peer_tags(self):
        span = Span("op")
        span.set_tag(SPAN_KIND, SpanKind.CLIENT)
        span.set_tag("peer.service", "my-db")
        span.set_tag("db.instance", "primary")
        result = _get_peer_tags(span, DEFAULT_PEER_TAG_KEYS)
        assert ("db.instance", "primary") in result
        assert ("peer.service", "my-db") in result

    def test_producer_span_extracts_peer_tags(self):
        span = Span("op")
        span.set_tag(SPAN_KIND, SpanKind.PRODUCER)
        span.set_tag("messaging.destination", "my-queue")
        result = _get_peer_tags(span, DEFAULT_PEER_TAG_KEYS)
        assert ("messaging.destination", "my-queue") in result

    def test_consumer_span_extracts_peer_tags(self):
        span = Span("op")
        span.set_tag(SPAN_KIND, SpanKind.CONSUMER)
        span.set_tag("topic", "events")
        result = _get_peer_tags(span, DEFAULT_PEER_TAG_KEYS)
        assert ("topic", "events") in result

    def test_server_span_returns_empty(self):
        span = Span("op")
        span.set_tag(SPAN_KIND, SpanKind.SERVER)
        span.set_tag("peer.service", "my-db")
        result = _get_peer_tags(span, DEFAULT_PEER_TAG_KEYS)
        assert result == ()

    def test_internal_span_returns_empty(self):
        span = Span("op")
        span.set_tag(SPAN_KIND, SpanKind.INTERNAL)
        span.set_tag("peer.service", "my-db")
        result = _get_peer_tags(span, DEFAULT_PEER_TAG_KEYS)
        assert result == ()

    def test_no_span_kind_returns_empty(self):
        span = Span("op")
        span.set_tag("peer.service", "my-db")
        result = _get_peer_tags(span, DEFAULT_PEER_TAG_KEYS)
        assert result == ()

    def test_base_service_included(self):
        span = Span("op")
        span.set_tag(SPAN_KIND, SpanKind.CLIENT)
        span.set_tag("_dd.base_service", "original-svc")
        result = _get_peer_tags(span, DEFAULT_PEER_TAG_KEYS)
        assert ("_dd.base_service", "original-svc") in result

    def test_no_matching_tags_returns_empty(self):
        span = Span("op")
        span.set_tag(SPAN_KIND, SpanKind.CLIENT)
        span.set_tag("some.other.tag", "value")
        result = _get_peer_tags(span, DEFAULT_PEER_TAG_KEYS)
        assert result == ()

    def test_custom_peer_tag_keys(self):
        span = Span("op")
        span.set_tag(SPAN_KIND, SpanKind.CLIENT)
        span.set_tag("custom.key", "custom-value")
        result = _get_peer_tags(span, ("custom.key",))
        assert result == (("custom.key", "custom-value"),)

    def test_tags_sorted_for_consistency(self):
        span = Span("op")
        span.set_tag(SPAN_KIND, SpanKind.CLIENT)
        span.set_tag("server.address", "host1")
        span.set_tag("db.name", "mydb")
        span.set_tag("peer.service", "svc")
        result = _get_peer_tags(span, DEFAULT_PEER_TAG_KEYS)
        keys = [k for k, _ in result]
        assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# is_trace_root (Trilean) tests
# ---------------------------------------------------------------------------
class TestIsTraceRoot:
    def test_root_span_is_trace_root_true(self):
        # Root spans have parent_id == None (Python) which maps to trace root
        span = Span("root")
        assert not span.parent_id  # parent_id is None for root spans
        key = _span_aggr_key(span)
        assert key[9] == _TRILEAN_TRUE

    def test_root_span_with_parent_id_zero_is_trace_root_true(self):
        span = Span("root", parent_id=0)
        key = _span_aggr_key(span)
        assert key[9] == _TRILEAN_TRUE

    def test_child_span_is_trace_root_false(self):
        parent = Span("parent")
        child = Span("child", parent_id=parent.span_id)
        key = _span_aggr_key(child)
        assert key[9] == _TRILEAN_FALSE


# ---------------------------------------------------------------------------
# _span_aggr_key tests (new dimensions)
# ---------------------------------------------------------------------------
class TestSpanAggrKey:
    def test_key_includes_span_kind(self):
        span = Span("op")
        span.set_tag(SPAN_KIND, SpanKind.SERVER)
        key = _span_aggr_key(span)
        assert key[8] == "server"

    def test_key_no_span_kind_is_empty_string(self):
        span = Span("op")
        key = _span_aggr_key(span)
        assert key[8] == ""

    def test_key_includes_grpc_status_code(self):
        span = Span("op")
        span.set_tag("grpc.status.code", "2")
        key = _span_aggr_key(span)
        assert key[11] == 2

    def test_key_includes_peer_tags(self):
        span = Span("op")
        span.set_tag(SPAN_KIND, SpanKind.CLIENT)
        span.set_tag("peer.service", "my-db")
        key = _span_aggr_key(span)
        assert ("peer.service", "my-db") in key[10]

    def test_different_span_kinds_produce_different_keys(self):
        span1 = Span("op")
        span1.set_tag(SPAN_KIND, SpanKind.CLIENT)
        span2 = Span("op")
        span2.set_tag(SPAN_KIND, SpanKind.SERVER)
        key1 = _span_aggr_key(span1)
        key2 = _span_aggr_key(span2)
        assert key1 != key2

    def test_different_grpc_codes_produce_different_keys(self):
        span1 = Span("op")
        span1.set_tag("grpc.status.code", "0")
        span2 = Span("op")
        span2.set_tag("grpc.status.code", "2")
        key1 = _span_aggr_key(span1)
        key2 = _span_aggr_key(span2)
        assert key1 != key2

    def test_key_is_hashable(self):
        span = Span("op")
        span.set_tag(SPAN_KIND, SpanKind.CLIENT)
        span.set_tag("peer.service", "my-db")
        span.set_tag("grpc.status.code", "2")
        key = _span_aggr_key(span)
        # Must be hashable for use as dict key
        hash(key)
        d = {key: True}
        assert d[key] is True

    def test_backward_compatible_fields(self):
        """Verify original 8 dimensions are still at the same positions."""
        span = Span("op", service="svc", resource="res", span_type="web")
        span.set_tag("http.status_code", "200")
        span.set_tag("http.method", "GET")
        span.set_tag("http.route", "/api/test")
        span.context.dd_origin = "synthetics"
        key = _span_aggr_key(span)
        assert key[0] == "op"  # name
        assert key[1] == "svc"  # service
        assert key[2] == "res"  # resource
        assert key[3] == "web"  # type
        assert key[4] == 200  # http status code
        assert key[5] is True  # synthetics
        assert key[6] == "GET"  # http method
        assert key[7] == "/api/test"  # http endpoint


# ---------------------------------------------------------------------------
# SpanStatsProcessorV06 eligibility tests
# ---------------------------------------------------------------------------
class TestSpanStatsProcessorEligibility:
    @mock.patch("ddtrace.internal.processor.stats.SpanStatsProcessorV06.start")
    def _make_processor(self, mock_start, **kwargs):
        return SpanStatsProcessorV06("http://localhost:8126", **kwargs)

    def test_span_kind_eligible_span_counted(self):
        """A span with span.kind=server should be counted even if not top-level or measured."""
        proc = self._make_processor()
        # Create a child span (not top-level) without measured flag
        parent = Span("parent")
        child = Span("child", parent_id=parent.span_id)
        child._parent = parent
        child.service = parent.service
        child.set_tag(SPAN_KIND, SpanKind.SERVER)
        child.start_ns = 0
        child.finish()
        proc.on_span_finish(child)

        assert len(proc._buckets) == 1
        bucket = list(proc._buckets.values())[0]
        assert len(bucket) == 1
        stats = list(bucket.values())[0]
        assert stats.hits == 1

    def test_internal_span_kind_not_eligible_alone(self):
        """A span with span.kind=internal should NOT be counted if not top-level and not measured."""
        proc = self._make_processor()
        parent = Span("parent")
        child = Span("child", parent_id=parent.span_id)
        child._parent = parent
        child._local_root = parent  # Set local_root so child is not considered top-level
        child.service = parent.service
        child.set_tag(SPAN_KIND, SpanKind.INTERNAL)
        child.start_ns = 0
        child.finish()
        proc.on_span_finish(child)

        # internal span should not create a bucket entry
        bucket = list(proc._buckets.values())
        total_entries = sum(len(b) for b in bucket)
        assert total_entries == 0

    def test_custom_peer_tag_keys(self):
        """Custom peer tag keys provided to the processor should be used."""
        proc = self._make_processor(peer_tag_keys=("custom.tag",))
        span = Span("op")
        span.set_tag(SPAN_KIND, SpanKind.CLIENT)
        span.set_tag("custom.tag", "value")
        span.set_tag("peer.service", "ignored")
        span.start_ns = 0
        span.finish()
        proc.on_span_finish(span)

        bucket = list(proc._buckets.values())[0]
        key = list(bucket.keys())[0]
        peer_tags = key[10]
        assert ("custom.tag", "value") in peer_tags
        # peer.service should not be in peer_tags since it's not in custom keys
        assert all(k != "peer.service" for k, _ in peer_tags)


# ---------------------------------------------------------------------------
# Serialization tests
# ---------------------------------------------------------------------------
class TestSerialization:
    @mock.patch("ddtrace.internal.processor.stats.SpanStatsProcessorV06.start")
    def _make_processor(self, mock_start, **kwargs):
        return SpanStatsProcessorV06("http://localhost:8126", **kwargs)

    def test_serialized_bucket_includes_span_kind(self):
        proc = self._make_processor()
        span = Span("op", service="svc")
        span.set_tag(SPAN_KIND, SpanKind.SERVER)
        span.start_ns = 0
        span.finish()
        proc.on_span_finish(span)

        buckets = proc._serialize_buckets()
        assert len(buckets) == 1
        stat = buckets[0]["Stats"][0]
        assert stat["SpanKind"] == "server"

    def test_serialized_bucket_includes_is_trace_root(self):
        proc = self._make_processor()
        span = Span("op", service="svc")
        span.start_ns = 0
        span.finish()
        proc.on_span_finish(span)

        buckets = proc._serialize_buckets()
        stat = buckets[0]["Stats"][0]
        assert stat["IsTraceRoot"] == _TRILEAN_TRUE

    def test_serialized_bucket_includes_peer_tags(self):
        proc = self._make_processor()
        span = Span("op", service="svc")
        span.set_tag(SPAN_KIND, SpanKind.CLIENT)
        span.set_tag("peer.service", "remote-svc")
        span.set_tag("db.name", "mydb")
        span.start_ns = 0
        span.finish()
        proc.on_span_finish(span)

        buckets = proc._serialize_buckets()
        stat = buckets[0]["Stats"][0]
        assert "PeerTags" in stat
        assert "db.name:mydb" in stat["PeerTags"]
        assert "peer.service:remote-svc" in stat["PeerTags"]

    def test_serialized_bucket_no_peer_tags_when_empty(self):
        proc = self._make_processor()
        span = Span("op", service="svc")
        span.set_tag(SPAN_KIND, SpanKind.SERVER)
        span.start_ns = 0
        span.finish()
        proc.on_span_finish(span)

        buckets = proc._serialize_buckets()
        stat = buckets[0]["Stats"][0]
        assert "PeerTags" not in stat

    def test_serialized_bucket_includes_grpc_status_code(self):
        proc = self._make_processor()
        span = Span("op", service="svc")
        span.set_tag("grpc.status.code", "14")
        span.start_ns = 0
        span.finish()
        proc.on_span_finish(span)

        buckets = proc._serialize_buckets()
        stat = buckets[0]["Stats"][0]
        assert stat["GRPCStatusCode"] == 14

    def test_serialized_bucket_no_grpc_status_code_when_zero(self):
        proc = self._make_processor()
        span = Span("op", service="svc")
        span.start_ns = 0
        span.finish()
        proc.on_span_finish(span)

        buckets = proc._serialize_buckets()
        stat = buckets[0]["Stats"][0]
        assert "GRPCStatusCode" not in stat

    def test_serialized_bucket_child_is_trace_root_false(self):
        proc = self._make_processor()
        parent = Span("parent", service="svc")
        child = Span("child", service="other-svc", parent_id=parent.span_id)
        child._parent = parent
        child.start_ns = 0
        child.finish()
        proc.on_span_finish(child)

        buckets = proc._serialize_buckets()
        stat = buckets[0]["Stats"][0]
        assert stat["IsTraceRoot"] == _TRILEAN_FALSE
