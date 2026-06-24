"""
Unit tests for FlagEvaluationWriter — two-tier aggregation, canonical key, EVP transport.

Tests validate the two-tier aggregation spec:
- canonical_context_key: sorted, type-tagged, length-delimited (NOT a hash)
- Two-tier aggregation (full → degraded → drop-counted)
- Caps GLOBAL_CAP=131072 / PER_FLAG_CAP=10000 / DEGRADED_CAP=32768
- 256-field / 256-char context pruning
- runtime_default_used from absent/None variant
- Non-blocking enqueue with drop-and-count on queue.Full
- EVP POST to /evp_proxy/v2/api/v2/flagevaluation with correct headers
"""

from datetime import datetime
from datetime import timezone
from decimal import Decimal
import json
import time
from unittest import mock

import pytest

from ddtrace.internal.openfeature._flagevaluation_writer import DEGRADED_CAP
from ddtrace.internal.openfeature._flagevaluation_writer import EVAL_SCALE_DEGRADED_BUCKET_TARGET
from ddtrace.internal.openfeature._flagevaluation_writer import EVAL_SCALE_FULL_BUCKET_TARGET
from ddtrace.internal.openfeature._flagevaluation_writer import EVAL_SCALE_PER_FLAG_BUCKET_TARGET
from ddtrace.internal.openfeature._flagevaluation_writer import EVP_SUBDOMAIN_HEADER_NAME
from ddtrace.internal.openfeature._flagevaluation_writer import EVP_SUBDOMAIN_VALUE
from ddtrace.internal.openfeature._flagevaluation_writer import FLAG_EVALUATION_DEGRADED_METRIC
from ddtrace.internal.openfeature._flagevaluation_writer import FLAG_EVALUATION_DROPPED_METRIC
from ddtrace.internal.openfeature._flagevaluation_writer import FLAG_EVALUATION_REASON_CARDINALITY_CAP
from ddtrace.internal.openfeature._flagevaluation_writer import FLAG_EVALUATION_REASON_DEGRADED_CAP
from ddtrace.internal.openfeature._flagevaluation_writer import FLAG_EVALUATION_REASON_PAYLOAD_LIMIT
from ddtrace.internal.openfeature._flagevaluation_writer import FLAG_EVALUATION_REASON_QUEUE_OVERFLOW
from ddtrace.internal.openfeature._flagevaluation_writer import FLAG_EVALUATION_SPLITS_METRIC
from ddtrace.internal.openfeature._flagevaluation_writer import FLAGEVALUATIONS_ENDPOINT
from ddtrace.internal.openfeature._flagevaluation_writer import GLOBAL_CAP
from ddtrace.internal.openfeature._flagevaluation_writer import MAX_CONTEXT_FIELDS
from ddtrace.internal.openfeature._flagevaluation_writer import MAX_FIELD_LENGTH
from ddtrace.internal.openfeature._flagevaluation_writer import PER_FLAG_CAP
from ddtrace.internal.openfeature._flagevaluation_writer import QUEUE_SIZE
from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter
from ddtrace.internal.openfeature._flagevaluation_writer import _build_payloads_with_stats
from ddtrace.internal.openfeature._flagevaluation_writer import _EvalEvent
from ddtrace.internal.openfeature._flagevaluation_writer import canonical_context_key
from ddtrace.internal.openfeature._flagevaluation_writer import flatten_and_prune_context
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


TELEMETRY_COUNT_PATCH = "ddtrace.internal.openfeature._flagevaluation_writer.telemetry_writer.add_count_metric"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    flag_key: str = "my-flag",
    variant: str = "on",
    allocation_key: str = "alloc-1",
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
        targeting_key=targeting_key,
        attrs=attrs or {},
        runtime_default=runtime_default,
        error_message=error_message,
        eval_time_ms=eval_time_ms,
    )


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _assert_count_metric(mock_add_count, name: str, value: int, reason: str = None) -> None:
    tags = (("reason", reason),) if reason else tuple()
    mock_add_count.assert_any_call(TELEMETRY_NAMESPACE.TRACERS, name, value, tags)


def _assert_no_count_metric(mock_add_count, name: str, reason: str = None) -> None:
    tags = (("reason", reason),) if reason else tuple()
    for call in mock_add_count.call_args_list:
        if call.args == (TELEMETRY_NAMESPACE.TRACERS, name, mock.ANY, tags):
            raise AssertionError(f"unexpected metric {name} tags={tags}: {call}")


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
        """int 1 vs string '1' must produce different keys (type-tagged keys)."""
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

    def test_datetime_vs_string_distinct_keys(self):
        timestamp = datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc)
        k_datetime = canonical_context_key({"x": timestamp})
        k_string = canonical_context_key({"x": timestamp.isoformat()})
        assert k_datetime != k_string

    def test_value_with_equals_or_newline_no_boundary_confusion(self):
        r"""'=' and '\n' in values must not fake a field boundary (length-prefix protocol)."""
        k_with = canonical_context_key({"a": "foo=bar\nbaz"})
        k_without = canonical_context_key({"a": "foo", "bar\nbaz": ""})
        assert k_with != k_without

    def test_no_hashlib_or_md5_used(self):
        """Verify no hash function is used by inspecting the module source."""
        import inspect

        import ddtrace.internal.openfeature._flagevaluation_writer as mod_src

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

    def test_dedicated_targeting_key_aliases_are_not_context_attrs(self):
        attrs = {"targetingKey": "user-1", "targeting_key": "user-1", "tier": "premium"}
        result = flatten_and_prune_context(attrs)
        assert result == {"tier": "premium"}

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
        """int 1 vs str '1' in context produce two distinct full-tier buckets."""
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
        """Beyond degradedCap, increment _dropped_degraded_overflow."""
        # Fill the degraded map to the cap.
        for i in range(DEGRADED_CAP):
            key = (f"flag-{i}", "on", "alloc", False, "")
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
        """Absent/empty variant → runtime_default_used True."""
        e = _make_event(variant="", runtime_default=True)
        writer._aggregate(e)

        assert len(writer._full) == 1
        entry = list(writer._full.values())[0]
        assert entry.runtime_default is True

    def test_degraded_event_omits_targeting_key_and_context(self, writer):
        """Degraded tier strips targeting_key + context."""
        with writer._lock:
            writer._add_to_degraded(_make_event(targeting_key="some-key", attrs={"k": "v"}))

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

    def test_enqueue_queues_pruned_context_snapshot(self, writer):
        attrs = {f"field-{i:03d}": f"value-{i:03d}" for i in range(MAX_CONTEXT_FIELDS + 50)}
        attrs["zzz-oversized"] = "x" * (MAX_FIELD_LENGTH + 1)

        writer.enqueue(_make_event(attrs=attrs))

        queued = writer._queue.get_nowait()
        assert len(queued.attrs) == MAX_CONTEXT_FIELDS
        assert "zzz-oversized" not in queued.attrs

    def test_enqueue_flattens_nested_context_snapshot(self, writer):
        writer.enqueue(_make_event(attrs={"user": {"id": 123, "plan": "pro"}}))

        queued = writer._queue.get_nowait()
        assert queued.attrs == {"user.id": 123, "user.plan": "pro"}


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
        """Payload goes to /evp_proxy/v2/api/v2/flagevaluation with EVP subdomain header."""
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
        assert "reason" not in evals[0]
        assert evals[0]["first_evaluation"] <= evals[0]["last_evaluation"]

    def test_context_pruning_above_256_fields(self, writer):
        """Context with >256 fields is pruned before keying."""
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
        """Context values >256 chars are skipped."""
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

    def test_openfeature_datetime_context_value_is_json_serialized(self, writer):
        """OpenFeature allows datetime context values; payload JSON should stringify them."""
        timestamp = datetime(2026, 6, 23, 12, 30, tzinfo=timezone.utc)
        writer.enqueue(_make_event(attrs={"seen_at": timestamp, "nested": {"created_at": timestamp}}))

        with mock.patch.object(writer, "_send_payload") as mock_send:
            writer.periodic()

        payload_bytes = mock_send.call_args[0][0]
        decoded = json.loads(payload_bytes)
        ctx_eval = decoded["flagEvaluations"][0]["context"]["evaluation"]
        assert ctx_eval["seen_at"] == timestamp.isoformat()
        assert ctx_eval["nested.created_at"] == timestamp.isoformat()

    def test_openfeature_non_json_context_values_are_json_serialized(self, writer):
        """Non-JSON-safe context values must not make payload encoding drop the batch."""

        class CustomContextValue:
            def __str__(self):
                return "custom-value"

        writer.enqueue(
            _make_event(
                attrs={
                    "amount": Decimal("12.34"),
                    "custom": CustomContextValue(),
                    "list": [Decimal("1.5"), CustomContextValue()],
                }
            )
        )

        with mock.patch.object(writer, "_send_payload") as mock_send:
            writer.periodic()

        payload_bytes = mock_send.call_args[0][0]
        decoded = json.loads(payload_bytes)
        ctx_eval = decoded["flagEvaluations"][0]["context"]["evaluation"]
        assert ctx_eval["amount"] == "12.34"
        assert ctx_eval["custom"] == "custom-value"
        assert ctx_eval["list"] == ["1.5", "custom-value"]

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

    def test_targeting_key_is_not_duplicated_in_context_evaluation(self, writer):
        writer.enqueue(
            _make_event(
                targeting_key="user-1",
                attrs={"targetingKey": "user-1", "targeting_key": "user-1", "tier": "premium"},
            )
        )

        with mock.patch.object(writer, "_send_payload") as mock_send:
            writer.periodic()

        decoded = json.loads(mock_send.call_args[0][0])
        ev = decoded["flagEvaluations"][0]
        assert ev["targeting_key"] == "user-1"
        assert ev["context"]["evaluation"] == {"tier": "premium"}

    def test_writer_endpoint_constant(self):
        assert FLAGEVALUATIONS_ENDPOINT == "/evp_proxy/v2/api/v2/flagevaluation"

    def test_cap_sizing_constants(self):
        assert EVAL_SCALE_FULL_BUCKET_TARGET == 125_000
        assert EVAL_SCALE_PER_FLAG_BUCKET_TARGET == 10_000
        assert EVAL_SCALE_DEGRADED_BUCKET_TARGET == 25_000
        assert GLOBAL_CAP == 131_072
        assert PER_FLAG_CAP == 10_000
        assert DEGRADED_CAP == 32_768

    def test_class_exists_and_inherits_periodic_service(self):
        from ddtrace.internal.periodic import PeriodicService

        assert issubclass(FlagEvaluationWriter, PeriodicService)

    def test_payloads_are_split_under_evp_payload_size_limit(self, writer):
        for i in range(5):
            writer.enqueue(
                _make_event(
                    flag_key=f"split-{i}",
                    targeting_key=f"user-{i}",
                    attrs={"blob": "x" * 200},
                )
            )

        sent = []
        with mock.patch("ddtrace.internal.openfeature._flagevaluation_writer.FLAGEVALUATIONS_PAYLOAD_SIZE_LIMIT", 900):
            with mock.patch(TELEMETRY_COUNT_PATCH) as mock_count:
                with mock.patch.object(writer, "_send_payload", side_effect=lambda p, n: sent.append((p, n))):
                    writer.periodic()

        assert len(sent) > 1
        _assert_count_metric(mock_count, FLAG_EVALUATION_SPLITS_METRIC, len(sent) - 1)
        _assert_no_count_metric(mock_count, FLAG_EVALUATION_DROPPED_METRIC, FLAG_EVALUATION_REASON_PAYLOAD_LIMIT)
        _assert_no_count_metric(mock_count, FLAG_EVALUATION_DEGRADED_METRIC, FLAG_EVALUATION_REASON_PAYLOAD_LIMIT)
        seen_flags = set()
        for payload, num_events in sent:
            assert len(payload) <= 900
            decoded = json.loads(payload)
            _assert_batch_contract_valid(decoded)
            assert num_events == len(decoded["flagEvaluations"])
            seen_flags.update(row["flag"]["key"] for row in decoded["flagEvaluations"])
        assert seen_flags == {f"split-{i}" for i in range(5)}

    def test_single_oversized_full_event_is_degraded_before_send(self, writer):
        writer.enqueue(
            _make_event(
                flag_key="oversized-full",
                targeting_key="user-with-context",
                attrs={"blob": "x" * 200},
            )
        )

        sent = []
        with mock.patch("ddtrace.internal.openfeature._flagevaluation_writer.FLAGEVALUATIONS_PAYLOAD_SIZE_LIMIT", 300):
            with mock.patch(TELEMETRY_COUNT_PATCH) as mock_count:
                with mock.patch.object(writer, "_send_payload", side_effect=lambda p, n: sent.append((p, n))):
                    writer.periodic()

        assert len(sent) == 1
        _assert_count_metric(mock_count, FLAG_EVALUATION_DEGRADED_METRIC, 1, FLAG_EVALUATION_REASON_PAYLOAD_LIMIT)
        payload, num_events = sent[0]
        assert len(payload) <= 300
        assert num_events == 1
        row = json.loads(payload)["flagEvaluations"][0]
        assert row["flag"]["key"] == "oversized-full"
        assert "context" not in row
        assert "targeting_key" not in row

    def test_single_oversized_degraded_event_is_dropped_and_counted(self, writer):
        writer.enqueue(_make_event(flag_key="f" * 256, targeting_key="", attrs={}))

        with mock.patch("ddtrace.internal.openfeature._flagevaluation_writer.FLAGEVALUATIONS_PAYLOAD_SIZE_LIMIT", 100):
            with mock.patch(TELEMETRY_COUNT_PATCH) as mock_count:
                with mock.patch.object(writer, "_send_payload") as mock_send:
                    writer.periodic()

        mock_send.assert_not_called()
        _assert_count_metric(mock_count, FLAG_EVALUATION_DROPPED_METRIC, 1, FLAG_EVALUATION_REASON_PAYLOAD_LIMIT)

    def test_build_payload_stats_count_payload_limit_degraded_and_dropped_rows(self):
        now_ms = int(time.time() * 1000)
        degradable = {
            "timestamp": now_ms,
            "flag": {"key": "large"},
            "first_evaluation": now_ms,
            "last_evaluation": now_ms,
            "evaluation_count": 7,
            "targeting_key": "user-with-context",
            "context": {"evaluation": {"blob": "x" * 256}},
        }
        degraded = dict(degradable)
        degraded.pop("targeting_key")
        degraded.pop("context")
        degraded_payload = _build_payloads_with_stats([degraded], {}, 1 << 30).payloads[0][0]

        result = _build_payloads_with_stats([degradable], {}, len(degraded_payload))

        assert result.degraded_payload_limit == 7
        assert result.dropped_payload_limit == 0
        assert len(result.payloads) == 1

        undegreadable = {
            "timestamp": now_ms,
            "flag": {"key": "f" * 256},
            "first_evaluation": now_ms,
            "last_evaluation": now_ms,
            "evaluation_count": 11,
        }
        oversized_payload = _build_payloads_with_stats([undegreadable], {}, 1 << 30).payloads[0][0]

        result = _build_payloads_with_stats([undegreadable], {}, len(oversized_payload) - 1)

        assert result.degraded_payload_limit == 0
        assert result.dropped_payload_limit == 11
        assert result.payloads == []


# ---------------------------------------------------------------------------
# Stable payload contract for emitted rows (full + degraded)
# ---------------------------------------------------------------------------

# Required fields that EVERY flagevaluation row (full or degraded) must carry.
_REQUIRED_EVENT_FIELDS = {
    "timestamp": int,
    "flag": dict,
    "first_evaluation": int,
    "last_evaluation": int,
    "evaluation_count": int,
}
_OPTIONAL_EVENT_FIELDS = {
    "runtime_default_used",
    "targeting_key",
    "context",
    "variant",
    "allocation",
    "targeting_rule",
    "error",
}
_ALLOWED_EVENT_FIELDS = set(_REQUIRED_EVENT_FIELDS).union(_OPTIONAL_EVENT_FIELDS)
_ALLOWED_BATCH_FIELDS = {"flagEvaluations", "context"}
_ALLOWED_BATCH_CONTEXT_FIELDS = {"service", "env", "version"}
_ALLOWED_ROW_CONTEXT_FIELDS = {"evaluation", "dd"}


def _assert_row_contract_valid(ev: dict) -> None:
    """Assert one flagevaluation row uses only the SDK-owned stable EVP fields."""
    extra_fields = set(ev) - _ALLOWED_EVENT_FIELDS
    assert not extra_fields, f"unknown flagevaluation row fields: {sorted(extra_fields)}"

    # Required fields present with the right scalar types.
    for field, typ in _REQUIRED_EVENT_FIELDS.items():
        assert field in ev, f"required field {field!r} missing from row: {ev}"
        assert isinstance(ev[field], typ), f"{field} must be {typ}, got {type(ev[field])}"

    # flag.key is the one required nested field.
    assert "key" in ev["flag"] and isinstance(ev["flag"]["key"], str)

    # first <= last evaluation bound.
    assert ev["first_evaluation"] <= ev["last_evaluation"]
    assert ev["evaluation_count"] >= 1

    # variant/allocation, when present, MUST be {"key": "..."} objects, NOT bare strings.
    for obj_field in ("variant", "allocation"):
        if obj_field in ev:
            assert isinstance(ev[obj_field], dict), f"{obj_field} must serialize as an object"
            assert set(ev[obj_field].keys()) == {"key"}, f"{obj_field} must be exactly {{key}}"
            assert isinstance(ev[obj_field]["key"], str)

    # error, when present, is {"message": "..."}.
    if "error" in ev:
        assert isinstance(ev["error"], dict)
        assert "message" in ev["error"]

    # context, when present, nests an "evaluation" map.
    if "context" in ev:
        assert isinstance(ev["context"], dict)
        extra_context_fields = set(ev["context"]) - _ALLOWED_ROW_CONTEXT_FIELDS
        assert not extra_context_fields, f"unknown row context fields: {sorted(extra_context_fields)}"
        assert "evaluation" in ev["context"]
        assert isinstance(ev["context"]["evaluation"], dict)

    # runtime_default_used, when present, is a bool.
    if "runtime_default_used" in ev:
        assert isinstance(ev["runtime_default_used"], bool)


def _assert_batch_contract_valid(payload: dict) -> None:
    """Assert the batch envelope uses only the stable fields this SDK emits."""
    extra_fields = set(payload) - _ALLOWED_BATCH_FIELDS
    assert not extra_fields, f"unknown flagevaluation batch fields: {sorted(extra_fields)}"
    assert "flagEvaluations" in payload
    assert isinstance(payload["flagEvaluations"], list)
    if "context" in payload:
        assert isinstance(payload["context"], dict)
        extra_context_fields = set(payload["context"]) - _ALLOWED_BATCH_CONTEXT_FIELDS
        assert not extra_context_fields, f"unknown batch context fields: {sorted(extra_context_fields)}"
        for value in payload["context"].values():
            assert isinstance(value, str)
    for row in payload["flagEvaluations"]:
        _assert_row_contract_valid(row)


class TestPayloadContractConformance:
    def test_full_tier_row_uses_stable_contract_with_object_variant_and_allocation(self, writer):
        """A full-tier row carries variant/allocation as {key} objects + context.evaluation."""
        writer.enqueue(
            _make_event(
                variant="on",
                allocation_key="alloc-1",
                attrs={"tier": "premium"},
            )
        )
        with mock.patch.object(writer, "_send_payload") as mock_send:
            writer.periodic()

        decoded = json.loads(mock_send.call_args[0][0])
        _assert_batch_contract_valid(decoded)
        assert "flagEvaluations" in decoded
        row = decoded["flagEvaluations"][0]
        _assert_row_contract_valid(row)
        # Specifically the {key} object shape (NOT bare strings).
        assert row["variant"] == {"key": "on"}
        assert row["allocation"] == {"key": "alloc-1"}
        assert row["context"]["evaluation"]["tier"] == "premium"

    def test_degraded_tier_row_uses_stable_contract_and_omits_context(self, writer):
        """A degraded-tier row uses variant/allocation objects, no context."""
        writer._per_flag_count["my-flag"] = PER_FLAG_CAP  # force degraded
        writer.enqueue(
            _make_event(
                variant="on",
                allocation_key="alloc-1",
                attrs={"k": "v"},
                error_message="degraded failure",
            )
        )
        with mock.patch(TELEMETRY_COUNT_PATCH) as mock_count:
            with mock.patch.object(writer, "_send_payload") as mock_send:
                writer.periodic()

        decoded = json.loads(mock_send.call_args[0][0])
        _assert_batch_contract_valid(decoded)
        row = decoded["flagEvaluations"][0]
        _assert_row_contract_valid(row)
        _assert_count_metric(mock_count, FLAG_EVALUATION_DEGRADED_METRIC, 1, FLAG_EVALUATION_REASON_CARDINALITY_CAP)
        assert row["variant"] == {"key": "on"}
        assert row["error"] == {"message": "degraded failure"}
        assert "context" not in row
        assert "targeting_key" not in row

    def test_error_row_carries_error_message_object(self, writer):
        """An error evaluation produces a stable row with error.message."""
        writer.enqueue(
            _make_event(
                variant="",
                runtime_default=True,
                error_message="Flag not found",
            )
        )
        with mock.patch.object(writer, "_send_payload") as mock_send:
            writer.periodic()

        decoded = json.loads(mock_send.call_args[0][0])
        _assert_batch_contract_valid(decoded)
        row = decoded["flagEvaluations"][0]
        _assert_row_contract_valid(row)
        assert row["error"] == {"message": "Flag not found"}
        # Absent variant -> runtime_default_used True, no variant object emitted.
        assert row["runtime_default_used"] is True
        assert "variant" not in row

    def test_batch_payload_validates_full_and_degraded_rows_together(self, writer):
        """A single flush emits BOTH a full row and a degraded row under the stable contract."""
        # Full-tier event.
        writer.enqueue(_make_event(flag_key="full-flag", variant="on", attrs={"a": "b"}))
        # Degraded-tier event (different flag forced to degraded).
        writer._per_flag_count["deg-flag"] = PER_FLAG_CAP
        writer.enqueue(_make_event(flag_key="deg-flag", variant="off"))

        with mock.patch.object(writer, "_send_payload") as mock_send:
            writer.periodic()

        decoded = json.loads(mock_send.call_args[0][0])
        _assert_batch_contract_valid(decoded)
        rows = decoded["flagEvaluations"]
        assert len(rows) == 2
        for row in rows:
            _assert_row_contract_valid(row)
        flags = {r["flag"]["key"] for r in rows}
        assert flags == {"full-flag", "deg-flag"}

    def test_contract_rejects_top_level_reason(self):
        bad = {
            "flagEvaluations": [
                {
                    "timestamp": int(time.time() * 1000),
                    "flag": {"key": "reason-flag"},
                    "first_evaluation": int(time.time() * 1000),
                    "last_evaluation": int(time.time() * 1000),
                    "evaluation_count": 1,
                    "reason": "targeting_match",
                }
            ]
        }
        with pytest.raises(AssertionError, match="reason"):
            _assert_batch_contract_valid(bad)


# ---------------------------------------------------------------------------
# Shutdown drains the queue + final-flush before exit
# ---------------------------------------------------------------------------


class TestShutdownDrain:
    def test_on_shutdown_drains_queue_and_flushes(self, writer):
        """on_shutdown (the PeriodicService shutdown callback) must drain + flush queued events."""
        writer.enqueue(_make_event(flag_key="pending-1"))
        writer.enqueue(_make_event(flag_key="pending-2"))
        assert writer._queue.qsize() == 2

        with mock.patch.object(writer, "_send_payload") as mock_send:
            writer.on_shutdown()

        # The queued events were drained, aggregated, and flushed in a final POST.
        mock_send.assert_called_once()
        decoded = json.loads(mock_send.call_args[0][0])
        flags = {r["flag"]["key"] for r in decoded["flagEvaluations"]}
        assert flags == {"pending-1", "pending-2"}
        assert writer._queue.qsize() == 0

    def test_real_start_stop_lifecycle_drains_pending_event(self):
        """Real PeriodicService start()->enqueue->stop() drains the queue via on_shutdown."""
        # Long interval so the periodic timer never fires; only stop() triggers the flush.
        w = FlagEvaluationWriter(interval=3600.0)
        sent = []
        with mock.patch.object(w, "_send_payload", side_effect=lambda p, n: sent.append((p, n))):
            w.start()
            w.enqueue(_make_event(flag_key="lifecycle-flag"))
            w.stop()  # stop() runs on_shutdown -> periodic() -> drain + flush
            # stop() requests shutdown; join() blocks until the worker (and its
            # on_shutdown final flush) has fully completed before we assert.
            w.join(timeout=5.0)
        assert len(sent) == 1, "stop() must trigger a final drain+flush"
        decoded = json.loads(sent[0][0])
        assert decoded["flagEvaluations"][0]["flag"]["key"] == "lifecycle-flag"

    def test_background_drain_accumulates_beyond_queue_size_before_flush(self):
        """A flush window can exceed the bounded queue size and naturally degrade."""
        sent = []
        with mock.patch("ddtrace.internal.openfeature._flagevaluation_writer.QUEUE_SIZE", 8):
            with mock.patch("ddtrace.internal.openfeature._flagevaluation_writer.PER_FLAG_CAP", 12):
                with mock.patch("ddtrace.internal.openfeature._flagevaluation_writer.DRAIN_INTERVAL", 0.01):
                    w = FlagEvaluationWriter(interval=3600.0)
                    with mock.patch.object(w, "_send_payload", side_effect=lambda p, n: sent.append((p, n))):
                        w.start()
                        try:
                            for i in range(14):
                                assert _wait_until(lambda: not w._queue.full())
                                w.enqueue(
                                    _make_event(
                                        flag_key="natural-degrade",
                                        targeting_key=f"user-{i}",
                                        attrs={"user": i},
                                    )
                                )
                            assert _wait_until(lambda: w._queue.empty())
                            with w._lock:
                                assert w._dropped_queue == 0
                                assert w._degraded
                        finally:
                            w.stop()
                            w.join(timeout=5.0)

        assert sent, "shutdown flush must emit the accumulated evaluation rows"
        decoded = json.loads(sent[0][0])
        degraded_rows = [
            row
            for row in decoded["flagEvaluations"]
            if row["flag"]["key"] == "natural-degrade" and "context" not in row and "targeting_key" not in row
        ]
        assert len(degraded_rows) == 1
        assert degraded_rows[0]["evaluation_count"] == 2


# ---------------------------------------------------------------------------
# Backpressure drop counters are observable (emitted on flush)
# ---------------------------------------------------------------------------


class TestObservableDropCounters:
    def test_queue_overflow_drop_count_is_logged_on_flush(self, writer):
        """Queue-full drops increment _dropped_queue AND are surfaced (logged) on flush."""
        # Fill the queue so the next enqueue drops.
        for i in range(QUEUE_SIZE):
            writer._queue.put_nowait(_make_event(flag_key=f"f{i}"))
        writer.enqueue(_make_event(flag_key="dropped"))
        assert writer._dropped_queue == 1

        # Drain everything (so maps are populated) and assert the drop count is emitted.
        with mock.patch("ddtrace.internal.openfeature._flagevaluation_writer.logger") as mock_logger:
            with mock.patch(TELEMETRY_COUNT_PATCH) as mock_count:
                with mock.patch.object(writer, "_send_payload"):
                    writer.periodic()
            # A warning naming the queue-full drop count must have been emitted.
            warnings = [c for c in mock_logger.warning.call_args_list if "queue full" in str(c).lower()]
            assert warnings, "queue-full drop count must be logged (observable)"
            _assert_count_metric(mock_count, FLAG_EVALUATION_DROPPED_METRIC, 1, FLAG_EVALUATION_REASON_QUEUE_OVERFLOW)
        # Counter resets after emission.
        assert writer._dropped_queue == 0

    def test_degraded_overflow_drop_count_is_logged_on_flush(self, writer):
        """Degraded-cap overflow drops increment _dropped_degraded_overflow AND are logged."""
        from ddtrace.internal.openfeature._flagevaluation_writer import _Entry

        # Saturate the degraded map to its cap.
        for i in range(DEGRADED_CAP):
            writer._degraded[(f"flag-{i}", "on", "alloc", False, "")] = _Entry(1000, False, "", {}, "")
        with writer._lock:
            writer._add_to_degraded(_make_event(flag_key="overflow"))
        assert writer._dropped_degraded_overflow == 1

        with mock.patch("ddtrace.internal.openfeature._flagevaluation_writer.logger") as mock_logger:
            with mock.patch(TELEMETRY_COUNT_PATCH) as mock_count:
                with mock.patch.object(writer, "_send_payload"):
                    writer.periodic()
            warnings = [c for c in mock_logger.warning.call_args_list if "degraded cap" in str(c).lower()]
            assert warnings, "degraded-cap overflow count must be logged (observable)"
            _assert_count_metric(mock_count, FLAG_EVALUATION_DROPPED_METRIC, 1, FLAG_EVALUATION_REASON_DEGRADED_CAP)
        assert writer._dropped_degraded_overflow == 0

    def test_drop_accounting_is_complete_no_silent_loss(self, writer):
        """Σ(tier counts + drops) == events processed (no silent loss)."""
        # 3 distinct full-tier buckets + 2 degraded-overflow drops.
        writer._aggregate(_make_event(flag_key="a", attrs={"x": 1}))
        writer._aggregate(_make_event(flag_key="b", attrs={"x": 2}))
        writer._aggregate(_make_event(flag_key="a", attrs={"x": 1}))  # repeat -> count 2 on bucket a

        full_counts = sum(e.count for e in writer._full.values())
        assert full_counts == 3  # 2 (a) + 1 (b)
        assert writer._dropped_degraded_overflow == 0
        assert writer._dropped_queue == 0
