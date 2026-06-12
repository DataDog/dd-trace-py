"""
Unit tests for FlagEvaluationWriter — two-tier aggregation, canonical key, EVP transport.

Tests validate the FANOUT-CONTRACT spec:
- canonical_context_key: sorted, type-tagged, length-delimited (NOT a hash)
- Two-tier aggregation (full → degraded → drop-counted)
- Caps GLOBAL_CAP=131072 / PER_FLAG_CAP=10000 / DEGRADED_CAP=32768
- 256-field / 256-char context pruning
- runtime_default_used from absent/None variant
- Non-blocking enqueue with drop-and-count on queue.Full
- EVP POST to /evp_proxy/v2/api/v2/flagevaluations with correct headers
"""

import json
import queue
import time
from unittest import mock

import pytest

from ddtrace.internal.openfeature._flagevaluation_writer import (
    DEGRADED_CAP,
    EVAL_TIMESTAMP_METADATA_KEY,
    FLAGEVALUATIONS_ENDPOINT,
    GLOBAL_CAP,
    MAX_CONTEXT_FIELDS,
    MAX_FIELD_LENGTH,
    PER_FLAG_CAP,
    QUEUE_SIZE,
    EVP_SUBDOMAIN_HEADER_NAME,
    EVP_SUBDOMAIN_VALUE,
    FlagEvaluationWriter,
    _EvalEvent,
    canonical_context_key,
    flatten_and_prune_context,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(
    flag_key: str = "my-flag",
    variant: str = "on",
    allocation_key: str = "alloc-1",
    reason: str = "TARGETING_MATCH",
    targeting_key: str = "user-1",
    attrs: dict = None,
    runtime_default: bool = False,
    error_message: str = "",
    eval_time_ms: int = None,
) -> _EvalEvent:
    if eval_time_ms is None:
        eval_time_ms = int(time.time() * 1000)
    return _EvalEvent(
        flag_key=flag_key,
        variant=variant,
        allocation_key=allocation_key,
        reason=reason,
        targeting_key=targeting_key,
        attrs=attrs or {},
        runtime_default=runtime_default,
        error_message=error_message,
        eval_time_ms=eval_time_ms,
    )


@pytest.fixture
def writer():
    """Create a FlagEvaluationWriter that is NOT started (no background thread)."""
    return FlagEvaluationWriter(interval=10.0)


# ---------------------------------------------------------------------------
# canonical_context_key tests
# ---------------------------------------------------------------------------

class TestCanonicalContextKey:
    def test_empty_attrs_returns_empty_string(self):
        assert canonical_context_key({}) == ""
        assert canonical_context_key(None) == ""

    def test_same_dict_same_key(self):
        attrs = {"user": "alice", "tier": "premium"}
        assert canonical_context_key(attrs) == canonical_context_key(attrs)

    def test_different_insertion_order_same_key(self):
        """Dict order must not affect the key (sorted)."""
        a = {"b": "2", "a": "1"}
        b = {"a": "1", "b": "2"}
        assert canonical_context_key(a) == canonical_context_key(b)

    def test_int_vs_string_distinct_keys(self):
        """int 1 vs string '1' must produce different keys (type-tagged, reviewer concern #3)."""
        k_int = canonical_context_key({"x": 1})
        k_str = canonical_context_key({"x": "1"})
        assert k_int != k_str, "int 1 and str '1' must not alias into the same bucket"

    def test_bool_vs_int_distinct_keys(self):
        k_bool = canonical_context_key({"x": True})
        k_int = canonical_context_key({"x": 1})
        assert k_bool != k_int

    def test_float_vs_int_distinct_keys(self):
        k_float = canonical_context_key({"x": 1.0})
        k_int = canonical_context_key({"x": 1})
        assert k_float != k_int

    def test_value_with_equals_or_newline_no_boundary_confusion(self):
        """'=' and '\n' in values must not fake a field boundary (length-prefix protocol)."""
        k_with = canonical_context_key({"a": "foo=bar\nbaz"})
        k_without = canonical_context_key({"a": "foo", "bar\nbaz": ""})
        assert k_with != k_without

    def test_no_hashlib_or_md5_used(self):
        """Verify no hash function is used by inspecting the module source."""
        import ddtrace.internal.openfeature._flagevaluation_writer as mod_src
        import inspect
        src = inspect.getsource(mod_src)
        assert "hashlib" not in src, "hashlib must not appear in the writer"
        assert "md5" not in src, "md5 must not appear in the writer"

    def test_returns_string_not_bytes(self):
        k = canonical_context_key({"x": "y"})
        assert isinstance(k, str)


# ---------------------------------------------------------------------------
# flatten_and_prune_context tests
# ---------------------------------------------------------------------------

class TestFlattenAndPruneContext:
    def test_empty_returns_empty(self):
        assert flatten_and_prune_context({}) == {}

    def test_flat_attrs_passthrough(self):
        attrs = {"a": "1", "b": 2}
        result = flatten_and_prune_context(attrs)
        assert result == attrs

    def test_nested_attrs_flattened(self):
        attrs = {"user": {"id": "u1", "tier": "free"}}
        result = flatten_and_prune_context(attrs)
        assert "user.id" in result
        assert "user.tier" in result
        assert result["user.id"] == "u1"

    def test_prune_beyond_256_fields(self):
        attrs = {str(i): str(i) for i in range(300)}
        result = flatten_and_prune_context(attrs)
        assert len(result) <= MAX_CONTEXT_FIELDS

    def test_prune_skips_oversized_string_values(self):
        long_val = "x" * (MAX_FIELD_LENGTH + 1)
        attrs = {"short": "ok", "long": long_val}
        result = flatten_and_prune_context(attrs)
        assert "short" in result
        assert "long" not in result

    def test_prune_deterministic_sorted_order(self):
        """Pruned result must be reproducible across repeated calls."""
        attrs = {str(i): str(i) for i in range(300)}
        r1 = flatten_and_prune_context(attrs)
        r2 = flatten_and_prune_context(attrs)
        assert r1 == r2

    def test_context_with_256_fields_not_pruned(self):
        attrs = {str(i): str(i) for i in range(256)}
        result = flatten_and_prune_context(attrs)
        assert len(result) == 256


# ---------------------------------------------------------------------------
# Aggregation tests (full → degraded → drop-counted)
# ---------------------------------------------------------------------------

class TestAggregation:
    def test_two_identical_evals_aggregate_into_one_bucket_count_2(self, writer):
        t0 = int(time.time() * 1000)
        t1 = t0 + 100
        e1 = _make_event(eval_time_ms=t0)
        e2 = _make_event(eval_time_ms=t1)
        writer._aggregate(e1)
        writer._aggregate(e2)

        assert len(writer._full) == 1
        entry = list(writer._full.values())[0]
        assert entry.count == 2
        assert entry.first_evaluation <= entry.last_evaluation
        assert entry.first_evaluation == t0
        assert entry.last_evaluation == t1

    def test_two_evals_differing_context_value_type_produce_two_buckets(self, writer):
        """int 1 vs str '1' in context → two distinct full-tier buckets (reviewer concern #3)."""
        e_int = _make_event(attrs={"x": 1})
        e_str = _make_event(attrs={"x": "1"})
        writer._aggregate(e_int)
        writer._aggregate(e_str)
        assert len(writer._full) == 2

    def test_full_tier_overflow_routes_to_degraded(self, writer):
        """Overflow past globalCap routes to the degraded tier."""
        writer._global_count = GLOBAL_CAP  # simulate full global cap

        # Inject per-flag count below PER_FLAG_CAP so only the global cap triggers.
        writer._per_flag_count["flag-x"] = 0

        e = _make_event(flag_key="flag-x", attrs={"unique": "ctx"})
        writer._aggregate(e)

        assert len(writer._full) == 0
        assert len(writer._degraded) == 1

    def test_degraded_overflow_increments_dropped_counter(self, writer):
        """Beyond degradedCap, increment _dropped_degraded_overflow (reviewer concern #8)."""
        # Fill the degraded map to the cap.
        for i in range(DEGRADED_CAP):
            key = (f"flag-{i}", "on", "alloc", "SPLIT")
            from ddtrace.internal.openfeature._flagevaluation_writer import _Entry
            writer._degraded[key] = _Entry(1000, False, "", {}, "")

        with writer._lock:
            writer._add_to_degraded(_make_event(flag_key="overflow-flag"))

        assert writer._dropped_degraded_overflow == 1

    def test_per_flag_cap_routes_to_degraded(self, writer):
        """Per-flag cap exceeded → route to degraded even when globalCap has room."""
        writer._per_flag_count["my-flag"] = PER_FLAG_CAP  # flag is at cap
        e = _make_event(flag_key="my-flag", attrs={"ctx": "x"})
        writer._aggregate(e)

        assert len(writer._degraded) == 1
        assert len(writer._full) == 0

    def test_runtime_default_when_variant_is_absent(self, writer):
        """Absent/empty variant → runtime_default_used True (reviewer concern #5)."""
        e = _make_event(variant="", runtime_default=True)
        writer._aggregate(e)

        assert len(writer._full) == 1
        entry = list(writer._full.values())[0]
        assert entry.runtime_default is True

    def test_degraded_event_omits_targeting_key_and_context(self, writer):
        """Degraded tier strips targeting_key + context (schema omitempty, reviewer concern #2)."""
        with writer._lock:
            writer._add_to_degraded(
                _make_event(targeting_key="some-key", attrs={"k": "v"})
            )

        entry = list(writer._degraded.values())[0]
        assert entry.targeting_key == ""
        assert entry.context_attrs == {}


# ---------------------------------------------------------------------------
# Enqueue non-blocking tests
# ---------------------------------------------------------------------------

class TestEnqueue:
    def test_enqueue_non_blocking_on_full_queue(self, writer):
        """When queue is full, enqueue must NOT block and must increment _dropped_queue."""
        # Fill the queue.
        for i in range(QUEUE_SIZE):
            writer._queue.put_nowait(_make_event(flag_key=f"f{i}"))

        # This should not raise and should not block.
        writer.enqueue(_make_event(flag_key="overflow"))
        assert writer._dropped_queue == 1

    def test_enqueue_succeeds_when_queue_has_capacity(self, writer):
        writer.enqueue(_make_event())
        assert writer._queue.qsize() == 1


# ---------------------------------------------------------------------------
# Periodic flush + EVP POST tests
# ---------------------------------------------------------------------------

class TestPeriodicFlush:
    def test_periodic_drains_queue_and_builds_payload(self, writer):
        writer.enqueue(_make_event())

        with mock.patch.object(writer, "_send_payload") as mock_send:
            writer.periodic()

        mock_send.assert_called_once()
        payload_bytes, num_events = mock_send.call_args[0]
        decoded = json.loads(payload_bytes)
        assert "flagEvaluations" in decoded
        assert len(decoded["flagEvaluations"]) == 1
        ev = decoded["flagEvaluations"][0]
        assert ev["flag"]["key"] == "my-flag"
        assert "first_evaluation" in ev
        assert "last_evaluation" in ev
        assert "evaluation_count" in ev
        assert ev["evaluation_count"] == 1

    def test_periodic_no_send_when_empty(self, writer):
        with mock.patch.object(writer, "_send_payload") as mock_send:
            writer.periodic()
        mock_send.assert_not_called()

    def test_periodic_resets_maps_after_flush(self, writer):
        writer.enqueue(_make_event())
        with mock.patch.object(writer, "_send_payload"):
            writer.periodic()
        assert writer._full == {}
        assert writer._degraded == {}
        assert writer._global_count == 0

    @mock.patch("ddtrace.internal.openfeature._flagevaluation_writer.get_connection")
    def test_post_to_correct_endpoint_with_evp_header(self, mock_get_conn, writer):
        """Payload goes to /evp_proxy/v2/api/v2/flagevaluations with EVP subdomain header."""
        mock_conn = mock.Mock()
        mock_resp = mock.Mock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"OK"
        mock_conn.getresponse.return_value = mock_resp
        mock_get_conn.return_value = mock_conn

        writer.enqueue(_make_event())
        writer.periodic()

        mock_get_conn.assert_called_once()
        mock_conn.request.assert_called_once()
        call_args = mock_conn.request.call_args
        method, endpoint, _payload, headers = call_args[0]
        assert method == "POST"
        assert endpoint == FLAGEVALUATIONS_ENDPOINT
        assert headers[EVP_SUBDOMAIN_HEADER_NAME] == EVP_SUBDOMAIN_VALUE
        assert "Content-Type" in headers

    def test_two_evals_same_dims_aggregate_count_2(self, writer):
        t0 = int(time.time() * 1000)
        writer.enqueue(_make_event(eval_time_ms=t0))
        writer.enqueue(_make_event(eval_time_ms=t0 + 50))

        with mock.patch.object(writer, "_send_payload") as mock_send:
            writer.periodic()

        payload_bytes = mock_send.call_args[0][0]
        decoded = json.loads(payload_bytes)
        evals = decoded["flagEvaluations"]
        assert len(evals) == 1
        assert evals[0]["evaluation_count"] == 2
        assert evals[0]["first_evaluation"] <= evals[0]["last_evaluation"]

    def test_context_pruning_above_256_fields(self, writer):
        """Context with >256 fields is pruned before keying (reviewer concern #1)."""
        attrs = {str(i): str(i) for i in range(300)}
        e = _make_event(attrs=attrs)
        writer.enqueue(e)

        with mock.patch.object(writer, "_send_payload") as mock_send:
            writer.periodic()

        payload_bytes = mock_send.call_args[0][0]
        decoded = json.loads(payload_bytes)
        ev = decoded["flagEvaluations"][0]
        # The context.evaluation map should exist but have ≤256 fields.
        assert "context" in ev
        assert len(ev["context"]["evaluation"]) <= MAX_CONTEXT_FIELDS

    def test_context_value_exceeding_256_chars_pruned(self, writer):
        """Context values >256 chars are skipped (reviewer concern #1)."""
        long_val = "x" * (MAX_FIELD_LENGTH + 10)
        attrs = {"short": "ok", "long_field": long_val}
        e = _make_event(attrs=attrs)
        writer.enqueue(e)

        with mock.patch.object(writer, "_send_payload") as mock_send:
            writer.periodic()

        payload_bytes = mock_send.call_args[0][0]
        decoded = json.loads(payload_bytes)
        ev = decoded["flagEvaluations"][0]
        ctx_eval = ev.get("context", {}).get("evaluation", {})
        assert "short" in ctx_eval
        assert "long_field" not in ctx_eval

    def test_degraded_event_has_no_context_or_targeting_key(self, writer):
        """Degraded-tier events must not include targeting_key or context fields."""
        # Force to degraded by saturating per-flag cap.
        writer._per_flag_count["my-flag"] = PER_FLAG_CAP

        e = _make_event(targeting_key="tgt-user", attrs={"k": "v"})
        writer.enqueue(e)

        with mock.patch.object(writer, "_send_payload") as mock_send:
            writer.periodic()

        payload_bytes = mock_send.call_args[0][0]
        decoded = json.loads(payload_bytes)
        ev = decoded["flagEvaluations"][0]
        assert "targeting_key" not in ev
        assert "context" not in ev

    def test_writer_endpoint_constant(self):
        assert FLAGEVALUATIONS_ENDPOINT == "/evp_proxy/v2/api/v2/flagevaluations"

    def test_class_exists_and_inherits_periodic_service(self):
        from ddtrace.internal.periodic import PeriodicService
        assert issubclass(FlagEvaluationWriter, PeriodicService)
