"""
Unit tests for FlagEvaluationWriter — two-tier aggregation, canonical key, EVP transport.

Tests validate the two-tier aggregation spec:
- canonical_context_key: sorted, type-tagged, length-delimited (NOT a hash)
- Two-tier aggregation (full → degraded → drop-counted)
- Caps GLOBAL_CAP=131072 / PER_FLAG_CAP=10000 / DEGRADED_CAP=32768
- Bounded immutable pre-queue context snapshotting and truncation telemetry
- runtime_default_used from absent/None variant
- Non-blocking enqueue with drop-and-count on queue.Full
- EVP POST to /evp_proxy/v2/api/v2/flagevaluation with correct headers
"""

from collections.abc import Mapping
from collections.abc import Sequence
from datetime import datetime
from datetime import timezone
import enum
import json
import logging
import os
import queue
import select
import threading
import time
import typing
from unittest import mock

import pytest

from ddtrace.internal.openfeature._flagevaluation_writer import CONTEXT_TRUNCATION_CYCLE
from ddtrace.internal.openfeature._flagevaluation_writer import CONTEXT_TRUNCATION_MAX_CONTEXT_FIELDS
from ddtrace.internal.openfeature._flagevaluation_writer import CONTEXT_TRUNCATION_MAX_KEY_LENGTH
from ddtrace.internal.openfeature._flagevaluation_writer import CONTEXT_TRUNCATION_MAX_LIST_ELEMENTS
from ddtrace.internal.openfeature._flagevaluation_writer import CONTEXT_TRUNCATION_MAX_SNAPSHOT_DEPTH
from ddtrace.internal.openfeature._flagevaluation_writer import CONTEXT_TRUNCATION_MAX_STRUCTURE_PROPERTIES
from ddtrace.internal.openfeature._flagevaluation_writer import CONTEXT_TRUNCATION_MAX_VALUE_LENGTH
from ddtrace.internal.openfeature._flagevaluation_writer import CONTEXT_TRUNCATION_MAX_VISITED_NODES
from ddtrace.internal.openfeature._flagevaluation_writer import CONTEXT_TRUNCATION_SNAPSHOT_ERROR
from ddtrace.internal.openfeature._flagevaluation_writer import CONTEXT_TRUNCATION_UNSUPPORTED_VALUE
from ddtrace.internal.openfeature._flagevaluation_writer import DEGRADED_CAP
from ddtrace.internal.openfeature._flagevaluation_writer import DRAIN_WORKER_JOIN_TIMEOUT
from ddtrace.internal.openfeature._flagevaluation_writer import EVAL_SCALE_DEGRADED_BUCKET_TARGET
from ddtrace.internal.openfeature._flagevaluation_writer import EVAL_SCALE_FULL_BUCKET_TARGET
from ddtrace.internal.openfeature._flagevaluation_writer import EVAL_SCALE_PER_FLAG_BUCKET_TARGET
from ddtrace.internal.openfeature._flagevaluation_writer import EVP_SUBDOMAIN_HEADER_NAME
from ddtrace.internal.openfeature._flagevaluation_writer import EVP_SUBDOMAIN_VALUE
from ddtrace.internal.openfeature._flagevaluation_writer import FLAG_EVALUATION_CONTEXT_TRUNCATED_METRIC
from ddtrace.internal.openfeature._flagevaluation_writer import FLAG_EVALUATION_DEGRADED_METRIC
from ddtrace.internal.openfeature._flagevaluation_writer import FLAG_EVALUATION_DROPPED_METRIC
from ddtrace.internal.openfeature._flagevaluation_writer import FLAG_EVALUATION_REASON_CARDINALITY_CAP
from ddtrace.internal.openfeature._flagevaluation_writer import FLAG_EVALUATION_REASON_CLOSED
from ddtrace.internal.openfeature._flagevaluation_writer import FLAG_EVALUATION_REASON_DEGRADED_CAP
from ddtrace.internal.openfeature._flagevaluation_writer import FLAG_EVALUATION_REASON_PAYLOAD_LIMIT
from ddtrace.internal.openfeature._flagevaluation_writer import FLAG_EVALUATION_REASON_PRE_QUEUE_OVERFLOW
from ddtrace.internal.openfeature._flagevaluation_writer import FLAG_EVALUATION_REASON_QUEUE_OVERFLOW
from ddtrace.internal.openfeature._flagevaluation_writer import FLAG_EVALUATION_REASON_SERIALIZATION_ERROR
from ddtrace.internal.openfeature._flagevaluation_writer import FLAG_EVALUATION_SPLITS_METRIC
from ddtrace.internal.openfeature._flagevaluation_writer import FLAGEVALUATIONS_ENDPOINT
from ddtrace.internal.openfeature._flagevaluation_writer import GLOBAL_CAP
from ddtrace.internal.openfeature._flagevaluation_writer import MAX_CONTEXT_FIELDS
from ddtrace.internal.openfeature._flagevaluation_writer import MAX_FIELD_LENGTH
from ddtrace.internal.openfeature._flagevaluation_writer import MAX_KEY_LENGTH
from ddtrace.internal.openfeature._flagevaluation_writer import MAX_LIST_ELEMENTS
from ddtrace.internal.openfeature._flagevaluation_writer import MAX_SNAPSHOT_DEPTH
from ddtrace.internal.openfeature._flagevaluation_writer import MAX_STRUCTURE_PROPERTIES
from ddtrace.internal.openfeature._flagevaluation_writer import MAX_VALUE_LENGTH
from ddtrace.internal.openfeature._flagevaluation_writer import MAX_VISITED_NODES
from ddtrace.internal.openfeature._flagevaluation_writer import PER_FLAG_CAP
from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter
from ddtrace.internal.openfeature._flagevaluation_writer import _build_payloads_with_stats
from ddtrace.internal.openfeature._flagevaluation_writer import _EvalEvent
from ddtrace.internal.openfeature._flagevaluation_writer import _flatten_sequence
from ddtrace.internal.openfeature._flagevaluation_writer import _json_dumps
from ddtrace.internal.openfeature._flagevaluation_writer import canonical_context_key
from ddtrace.internal.openfeature._flagevaluation_writer import flatten_and_prune_context
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.threads import PeriodicThread


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
        attrs=attrs if attrs is not None else {},
        runtime_default=runtime_default,
        error_message=error_message,
        eval_time_ms=eval_time_ms,
    )


class _UnsafeLeaf:
    """A caller value the walker must reject rather than coerce with str()."""

    def __str__(self) -> str:
        raise AssertionError("arbitrary leaf conversion must not run")


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


def _json_dumps_rejecting_invalid_row(obj: typing.Any) -> bytes:
    if isinstance(obj, dict) and obj.get("flag", {}).get("key") == "invalid":
        raise TypeError("invalid row")
    return _json_dumps(obj)


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

    def test_equal_but_distinct_snapshots_share_one_key(self):
        """Two separate evaluations with equal context must aggregate into one bucket.

        Each evaluation builds its own snapshot object. The key must depend only on
        the content, so it cannot vary with object identity, hash randomization, or
        the order the walk happened to emit fields.
        """
        timestamp = datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc)
        raw = {
            "zeta": "z",
            "alpha": 1,
            "enabled": True,
            "ratio": 1.5,
            "missing": None,
            "when": timestamp,
            "nested": {"b": "2", "a": "1"},
            "tags": ["x", "y"],
        }
        reordered = {
            "tags": ["x", "y"],
            "nested": {"a": "1", "b": "2"},
            "when": timestamp,
            "missing": None,
            "ratio": 1.5,
            "enabled": True,
            "alpha": 1,
            "zeta": "z",
        }

        first, _ = flatten_and_prune_context(raw)
        second, _ = flatten_and_prune_context(reordered)

        assert first is not second
        assert canonical_context_key(first) == canonical_context_key(second)

    def test_key_is_stable_across_repeated_calls(self):
        """Repeated calls on equal content must return the identical key every time."""
        keys = {canonical_context_key({"b": "2", "a": 1, "c": True}) for _ in range(50)}
        assert len(keys) == 1

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
        datetime_snapshot, _ = flatten_and_prune_context({"x": timestamp})
        string_snapshot, _ = flatten_and_prune_context({"x": timestamp.isoformat()})
        assert canonical_context_key(datetime_snapshot) != canonical_context_key(string_snapshot)

    def test_value_with_equals_or_newline_no_boundary_confusion(self):
        r"""'=' and '\n' in values must not fake a field boundary (length-prefix protocol)."""
        k_with = canonical_context_key({"a": "foo=bar\nbaz"})
        k_without = canonical_context_key({"a": "foo", "bar\nbaz": ""})
        assert k_with != k_without


# ---------------------------------------------------------------------------
# flatten_and_prune_context tests
# ---------------------------------------------------------------------------


class TestFlattenAndPruneContext:
    def test_empty_returns_immutable_empty_snapshot(self):
        snapshot, reasons = flatten_and_prune_context({})
        assert snapshot == {}
        assert reasons == frozenset()
        with pytest.raises(TypeError):
            snapshot["new"] = "value"

    def test_caller_mutation_after_snapshot_does_not_change_the_snapshot(self):
        """The snapshot must detach from the caller's own containers.

        The hook returns control to the caller immediately after enqueue. The caller
        is free to reuse or mutate the context object it passed. The queued snapshot
        must keep the values that were present at evaluation time.
        """
        nested = {"inner": "original"}
        elements = ["first"]
        attrs = {"scalar": "original", "nested": nested, "list": elements}

        snapshot, _ = flatten_and_prune_context(attrs)

        attrs["scalar"] = "mutated"
        attrs["added"] = "late"
        del attrs["nested"]
        nested["inner"] = "mutated"
        elements[0] = "mutated"
        elements.append("late")

        assert snapshot == {
            "scalar": "original",
            "nested.inner": "original",
            "list[0]": "first",
        }

    def test_enqueued_event_keeps_the_context_present_at_evaluation_time(self, writer):
        """enqueue must buffer the snapshot, not a reference to the caller's mapping."""
        nested = {"inner": "original"}
        attrs = {"scalar": "original", "nested": nested}

        writer.enqueue(_make_event(attrs=attrs))

        attrs["scalar"] = "mutated"
        nested["inner"] = "mutated"

        queued = writer._queue.get_nowait()
        assert queued.attrs == {"scalar": "original", "nested.inner": "original"}
        with pytest.raises(TypeError):
            queued.attrs["new"] = "value"

    def test_in_bounds_without_lists_preserves_existing_flattened_values(self):
        timestamp = datetime(2026, 6, 23, 12, 30, tzinfo=timezone.utc)
        attrs = {
            "a": "1",
            "b": 2,
            "enabled": True,
            "missing": None,
            "user": {"id": "u1", "seen_at": timestamp},
        }

        snapshot, reasons = flatten_and_prune_context(attrs)

        assert dict(snapshot) == {
            "a": "1",
            "b": 2,
            "enabled": True,
            "user.id": "u1",
            "user.seen_at": timestamp.isoformat(),
        }
        assert reasons == frozenset()

    def test_null_leaves_are_omitted_without_a_truncation_reason(self):
        """A null attribute carries no value, so it is omitted rather than retained.

        dd-trace-rb's bounded_flatten drops nil leaves the same way. Retaining them
        here would place the identical caller context in a different aggregation
        bucket per language. Omission is by design, so no truncation is reported.
        """
        snapshot, reasons = flatten_and_prune_context(
            {"kept": 1, "missing": None, "nested": {"gone": None, "here": "v"}, "items": [None, "v"]}
        )

        assert dict(snapshot) == {"kept": 1, "nested.here": "v", "items[1]": "v"}
        assert reasons == frozenset()

    def test_null_only_context_snapshots_empty(self):
        snapshot, reasons = flatten_and_prune_context({"a": None, "b": None})

        assert dict(snapshot) == {}
        assert reasons == frozenset()

    def test_current_list_notation_is_explicitly_bracket_indexes(self):
        attrs = {"cohorts": ["beta", {"name": "ga"}], "user": {"roles": ["admin"]}}
        snapshot, reasons = flatten_and_prune_context(attrs)

        assert dict(snapshot) == {
            "cohorts[0]": "beta",
            "cohorts[1].name": "ga",
            "user.roles[0]": "admin",
        }
        assert "cohorts.0" not in snapshot
        assert reasons == frozenset()

    def test_cycle_is_reported_but_shared_subtree_is_not_a_cycle(self):
        shared = {"name": "kept"}
        cyclic = {"name": "also-kept"}
        cyclic["self"] = cyclic
        attrs = {"left": shared, "right": shared, "cyclic": cyclic}

        snapshot, reasons = flatten_and_prune_context(attrs)

        assert dict(snapshot) == {
            "left.name": "kept",
            "right.name": "kept",
            "cyclic.name": "also-kept",
        }
        assert reasons == frozenset((CONTEXT_TRUNCATION_CYCLE,))

    def test_dedicated_targeting_key_aliases_are_not_context_attrs(self):
        attrs = {"targetingKey": "user-1", "targeting_key": "user-1", "tier": "premium"}
        snapshot, reasons = flatten_and_prune_context(attrs)
        assert snapshot == {"tier": "premium"}
        assert reasons == frozenset()

    def test_context_field_cap_keeps_first_fields_in_insertion_order(self):
        attrs = {f"field-{i:03d}": i for i in reversed(range(MAX_CONTEXT_FIELDS + 1))}

        snapshot, reasons = flatten_and_prune_context(attrs)

        expected = [f"field-{i:03d}" for i in reversed(range(1, MAX_CONTEXT_FIELDS + 1))]
        assert list(snapshot) == expected
        assert "field-000" not in snapshot
        assert CONTEXT_TRUNCATION_MAX_CONTEXT_FIELDS in reasons

    def test_context_field_cap_stops_without_walking_complete_mapping(self):
        class BoundedIterationDict(dict):
            def __iter__(self) -> typing.Iterator[str]:
                for index, key in enumerate(super().__iter__()):
                    if index > MAX_CONTEXT_FIELDS:
                        raise AssertionError("walked beyond bounded retained subset")
                    yield key

        attrs = BoundedIterationDict((f"field-{i}", i) for i in range(10_000))
        snapshot, reasons = flatten_and_prune_context(attrs)

        assert len(snapshot) == MAX_CONTEXT_FIELDS
        assert CONTEXT_TRUNCATION_MAX_CONTEXT_FIELDS in reasons

    @pytest.mark.parametrize("failing_probe", [False, True])
    def test_context_field_cap_is_terminal_across_recursive_unwind(self, failing_probe):
        class NestedMapping(Mapping):
            def __init__(self) -> None:
                self.iterated: list[str] = []

            def __getitem__(self, key: str) -> str:
                return "nested-value"

            def __iter__(self) -> typing.Iterator[str]:
                self.iterated.append("kept")
                yield "kept"
                if failing_probe:
                    raise RuntimeError("first post-cap probe")
                self.iterated.append("extra")
                yield "extra"

            def __len__(self) -> int:
                raise AssertionError("len must not be called")

        class RootMapping(Mapping):
            def __init__(self, nested: NestedMapping) -> None:
                self.nested = nested
                self.iterated: list[str] = []

            def __getitem__(self, key: str) -> typing.Any:
                return self.nested if key == "nested" else "value"

            def __iter__(self) -> typing.Iterator[str]:
                for index in range(MAX_CONTEXT_FIELDS - 1):
                    key = f"field-{index}"
                    self.iterated.append(key)
                    yield key
                self.iterated.append("nested")
                yield "nested"
                self.iterated.append("root-extra")
                yield "root-extra"

            def __len__(self) -> int:
                raise AssertionError("len must not be called")

        nested = NestedMapping()
        root = RootMapping(nested)
        snapshot, reasons = flatten_and_prune_context(root)

        assert len(snapshot) == MAX_CONTEXT_FIELDS
        assert snapshot["nested.kept"] == "nested-value"
        assert reasons == frozenset((CONTEXT_TRUNCATION_MAX_CONTEXT_FIELDS,))
        assert root.iterated[-1] == "nested"
        assert nested.iterated == (["kept"] if failing_probe else ["kept", "extra"])

    def test_key_length_cap(self):
        snapshot, reasons = flatten_and_prune_context({"kept": "yes", "k" * (MAX_KEY_LENGTH + 1): "no"})
        assert snapshot == {"kept": "yes"}
        assert reasons == frozenset((CONTEXT_TRUNCATION_MAX_KEY_LENGTH,))

    def test_nested_key_length_is_checked_before_concatenation(self):
        prefix = "p" * MAX_KEY_LENGTH
        snapshot, reasons = flatten_and_prune_context({prefix: {"oversized-child": "no"}})
        assert snapshot == {}
        assert reasons == frozenset((CONTEXT_TRUNCATION_MAX_KEY_LENGTH,))

    def test_sequence_key_length_is_checked_before_concatenation(self):
        class FormatGuard(str):
            def __format__(self, format_spec: str) -> str:
                raise AssertionError("overlong sequence prefix must not be formatted")

        output: dict[str, typing.Any] = {}
        reasons: set[str] = set()
        _flatten_sequence(
            FormatGuard("p" * MAX_KEY_LENGTH),
            ["no"],
            output,
            set(),
            1,
            reasons,
            [MAX_VISITED_NODES],
        )

        assert output == {}
        assert reasons == {CONTEXT_TRUNCATION_MAX_KEY_LENGTH}

    def test_value_length_cap(self):
        snapshot, reasons = flatten_and_prune_context({"kept": "yes", "long": "v" * (MAX_VALUE_LENGTH + 1)})
        assert snapshot == {"kept": "yes"}
        assert reasons == frozenset((CONTEXT_TRUNCATION_MAX_VALUE_LENGTH,))

    def test_list_element_cap_bounds_inspected_discarded_values(self):
        discarded = "v" * (MAX_VALUE_LENGTH + 1)
        snapshot, reasons = flatten_and_prune_context({"values": [discarded] * (MAX_LIST_ELEMENTS + 1)})
        assert snapshot == {}
        assert CONTEXT_TRUNCATION_MAX_LIST_ELEMENTS in reasons

    def test_structure_property_cap_bounds_inspected_discarded_values(self):
        discarded = "v" * (MAX_VALUE_LENGTH + 1)
        nested = {f"key-{i}": discarded for i in range(MAX_STRUCTURE_PROPERTIES + 1)}
        snapshot, reasons = flatten_and_prune_context({"root": nested})
        assert snapshot == {}
        assert CONTEXT_TRUNCATION_MAX_STRUCTURE_PROPERTIES in reasons

    def test_snapshot_depth_cap_retains_scalars_at_depth_four(self):
        """A retained leaf sits at no more than MAX_SNAPSHOT_DEPTH path segments.

        dd-trace-rb's bounded_flatten applies the same boundary, so the identical
        caller context must flatten to the identical key set in both SDKs.
        """
        attrs = {"root": {"one": {"two": {"three": "kept", "deeper": {"leaf": "no"}}}}}

        snapshot, reasons = flatten_and_prune_context(attrs)

        assert snapshot == {"root.one.two.three": "kept"}
        assert CONTEXT_TRUNCATION_MAX_SNAPSHOT_DEPTH in reasons

    def test_visited_node_budget_matches_dd_trace_rb(self):
        assert MAX_VISITED_NODES == MAX_CONTEXT_FIELDS * (MAX_SNAPSHOT_DEPTH + 1)

    def test_visited_node_budget_exhaustion(self):
        level = {f"leaf-{i}": "v" * (MAX_VALUE_LENGTH + 1) for i in range(MAX_STRUCTURE_PROPERTIES)}
        for depth in range(MAX_SNAPSHOT_DEPTH - 1):
            level = {f"level-{depth}-{i}": level for i in range(MAX_STRUCTURE_PROPERTIES)}

        snapshot, reasons = flatten_and_prune_context(level)

        assert len(snapshot) < MAX_CONTEXT_FIELDS
        assert CONTEXT_TRUNCATION_MAX_VISITED_NODES in reasons

    def test_omitted_null_leaves_cannot_drive_an_unbounded_walk(self):
        """Null leaves are omitted, so no output-derived cap can bound this shape.

        Without the visited-node budget the walk costs MAX_STRUCTURE_PROPERTIES **
        (MAX_SNAPSHOT_DEPTH + 1) nodes: output never grows, so the global field cap never
        trips, and the width caps are per-container. Siblings alias one shared child so
        the input stays cheap to build; aliasing siblings is not a cycle, so the cycle cap
        does not fire either.
        """
        level: typing.Any = {f"leaf-{i}": None for i in range(MAX_STRUCTURE_PROPERTIES)}
        for _ in range(MAX_SNAPSHOT_DEPTH):
            level = {f"k{i}": level for i in range(MAX_STRUCTURE_PROPERTIES)}

        started = time.time()
        snapshot, reasons = flatten_and_prune_context(level)
        elapsed = time.time() - started

        assert snapshot == {}
        assert CONTEXT_TRUNCATION_MAX_VISITED_NODES in reasons
        # Generous bound: the budget caps this at MAX_VISITED_NODES nodes, and the
        # unbounded walk this guards against ran for hours.
        assert elapsed < 5.0

    def test_exact_visited_budget_does_not_report_truncation(self):
        with mock.patch("ddtrace.internal.openfeature._flagevaluation_writer.MAX_VISITED_NODES", 1):
            snapshot, reasons = flatten_and_prune_context({"kept": "yes"})
        assert snapshot == {"kept": "yes"}
        assert reasons == frozenset()

    def test_targeting_key_alias_consumes_visited_budget_before_filtering(self):
        attrs = {"targetingKey": "ignored", "kept": "not-inspected"}
        with mock.patch("ddtrace.internal.openfeature._flagevaluation_writer.MAX_VISITED_NODES", 1):
            snapshot, reasons = flatten_and_prune_context(attrs)
        assert snapshot == {}
        assert CONTEXT_TRUNCATION_MAX_VISITED_NODES in reasons

    def test_visited_budget_uses_only_one_exhaustion_lookahead(self):
        class TrackingSequence(Sequence):
            def __init__(self):
                self.accessed = []

            def __getitem__(self, index):
                self.accessed.append(index)
                return "not-inspected"

            def __len__(self):
                raise AssertionError("len must not be called")

        values = TrackingSequence()
        with mock.patch("ddtrace.internal.openfeature._flagevaluation_writer.MAX_VISITED_NODES", 1):
            snapshot, reasons = flatten_and_prune_context({"values": values})
        assert snapshot == {}
        assert CONTEXT_TRUNCATION_MAX_VISITED_NODES in reasons
        assert values.accessed == [0]

    def test_visited_budget_allows_only_one_successful_unwind_lookahead(self):
        class TrackingSequence(Sequence):
            def __init__(self, values: list[typing.Any]) -> None:
                self.values = values
                self.accessed: list[int] = []

            def __getitem__(self, index: int) -> typing.Any:
                self.accessed.append(index)
                if index >= len(self.values):
                    raise IndexError(index)
                return self.values[index]

            def __len__(self) -> int:
                raise AssertionError("len must not be called")

        inner = TrackingSequence(["first-extra", "inner-extra"])
        outer = TrackingSequence([inner, "outer-extra"])
        attrs = {"values": outer, "root-extra": "not-inspected"}

        with mock.patch("ddtrace.internal.openfeature._flagevaluation_writer.MAX_VISITED_NODES", 2):
            snapshot, reasons = flatten_and_prune_context(attrs)

        assert snapshot == {}
        assert reasons == frozenset((CONTEXT_TRUNCATION_MAX_VISITED_NODES,))
        assert outer.accessed == [0]
        assert inner.accessed == [0]

    def test_snapshot_depth_cap_drops_a_fifth_segment(self):
        snapshot, reasons = flatten_and_prune_context({"a": {"b": {"c": {"d": {"e": "x"}}}}})

        assert snapshot == {}
        assert CONTEXT_TRUNCATION_MAX_SNAPSHOT_DEPTH in reasons

    def test_nested_sequences_share_the_depth_boundary(self):
        assert flatten_and_prune_context({"l": [["a"]]})[0] == {"l[0][0]": "a"}

        snapshot, reasons = flatten_and_prune_context({"l": [[[["deep"]]]]})

        assert snapshot == {}
        assert CONTEXT_TRUNCATION_MAX_SNAPSHOT_DEPTH in reasons

    def test_visited_budget_drops_a_scalar_after_the_budget_is_spent(self):
        """Exhausting the budget on earlier fields drops a later scalar.

        Verified against dd-trace-rb: bounded_context_snapshot on this same shape also
        returns {} and reports max_visited_nodes, so the retained key set matches per SDK.
        Charging is positional, so a caller that front-loads wide containers loses the
        fields behind them -- that is the cost of bounding the walk at all.
        """
        # Five exact-width lists plus six root entries inspect 1,286 entries.
        discarded = [object()] * MAX_LIST_ELEMENTS
        attrs = {f"discarded-{index}": discarded for index in range(5)}
        attrs["kept"] = "yes"

        snapshot, reasons = flatten_and_prune_context(attrs)

        assert snapshot == {}
        assert CONTEXT_TRUNCATION_MAX_VISITED_NODES in reasons
        # Python also reports unsupported_value, which dd-trace-rb omits. Kept
        # deliberately: it is additional operator visibility into type-rejected fields,
        # and reasons are local advisory telemetry, so the retained key set still matches.
        assert reasons == frozenset((CONTEXT_TRUNCATION_MAX_VISITED_NODES, CONTEXT_TRUNCATION_UNSUPPORTED_VALUE))

    def test_snapshot_caps_match_the_cross_sdk_derivation(self):
        """These bounds are mirrored in dd-trace-rb, dd-trace-java, and dd-trace-go.

        Changing a value here without changing it in the other SDKs makes the same
        customer context produce different payloads per language, so the derivation
        is asserted rather than left implicit.
        """
        assert MAX_CONTEXT_FIELDS == MAX_KEY_LENGTH == MAX_VALUE_LENGTH == 256
        assert MAX_LIST_ELEMENTS == MAX_STRUCTURE_PROPERTIES == 256
        assert MAX_SNAPSHOT_DEPTH == 4
        assert (GLOBAL_CAP, PER_FLAG_CAP, DEGRADED_CAP) == (131_072, 10_000, 32_768)
        assert EVAL_SCALE_FULL_BUCKET_TARGET == 125_000
        assert EVAL_SCALE_PER_FLAG_BUCKET_TARGET == 10_000
        assert EVAL_SCALE_DEGRADED_BUCKET_TARGET == 25_000

    def test_exact_caps_do_not_report_truncation(self):
        attrs = {str(i): "v" for i in range(MAX_CONTEXT_FIELDS)}
        snapshot, reasons = flatten_and_prune_context(attrs)
        assert len(snapshot) == MAX_CONTEXT_FIELDS
        assert reasons == frozenset()

    def test_cap_plus_one_reports_context_and_mapping_width(self):
        attrs = {str(i): "v" for i in range(MAX_CONTEXT_FIELDS + 1)}
        snapshot, reasons = flatten_and_prune_context(attrs)
        assert len(snapshot) == MAX_CONTEXT_FIELDS
        assert reasons == frozenset(
            (CONTEXT_TRUNCATION_MAX_CONTEXT_FIELDS, CONTEXT_TRUNCATION_MAX_STRUCTURE_PROPERTIES)
        )

    def test_list_exact_cap_and_cap_plus_one_boundaries(self):
        exact_snapshot, exact_reasons = flatten_and_prune_context({"values": ["v"] * MAX_LIST_ELEMENTS})
        truncated_snapshot, truncated_reasons = flatten_and_prune_context({"values": ["v"] * (MAX_LIST_ELEMENTS + 1)})
        assert len(exact_snapshot) == len(truncated_snapshot) == MAX_LIST_ELEMENTS
        assert exact_reasons == frozenset()
        assert truncated_reasons == frozenset(
            (CONTEXT_TRUNCATION_MAX_CONTEXT_FIELDS, CONTEXT_TRUNCATION_MAX_LIST_ELEMENTS)
        )

    @pytest.mark.parametrize("discarded_key", ["targetingKey", "targeting_key"])
    def test_root_targeting_key_aliases_consume_structure_width(self, discarded_key):
        class DuplicateKeyMapping(Mapping):
            def __getitem__(self, key):
                return "ignored"

            def __iter__(self):
                for _ in range(MAX_STRUCTURE_PROPERTIES + 1):
                    yield discarded_key

            def __len__(self):
                raise AssertionError("len must not be called")

        snapshot, reasons = flatten_and_prune_context(DuplicateKeyMapping())
        assert snapshot == {}
        assert reasons == frozenset((CONTEXT_TRUNCATION_MAX_STRUCTURE_PROPERTIES,))

    def test_root_overlong_keys_consume_structure_width(self):
        attrs = {f"{'k' * MAX_KEY_LENGTH}{index}": "ignored" for index in range(MAX_STRUCTURE_PROPERTIES + 1)}
        snapshot, reasons = flatten_and_prune_context(attrs)
        assert snapshot == {}
        assert reasons == frozenset((CONTEXT_TRUNCATION_MAX_KEY_LENGTH, CONTEXT_TRUNCATION_MAX_STRUCTURE_PROPERTIES))

    def test_false_and_raising_len_mappings_are_iterated_without_truthiness_or_len(self):
        class FalseMapping(Mapping):
            def __getitem__(self, key):
                if key == "kept":
                    return "yes"
                raise KeyError(key)

            def __iter__(self):
                yield "kept"

            def __len__(self):
                return 0

        class RaisingLenMapping(FalseMapping):
            def __len__(self):
                raise AssertionError("len must not be called")

        false_snapshot, false_reasons = flatten_and_prune_context(FalseMapping())
        raising_snapshot, raising_reasons = flatten_and_prune_context(RaisingLenMapping())
        assert false_snapshot == raising_snapshot == {"kept": "yes"}
        assert false_reasons == raising_reasons == frozenset()

    def test_mapping_failing_width_lookahead_preserves_prefix_and_stops(self):
        class FailingLookaheadMapping(Mapping):
            def __init__(self):
                self.accessed = []

            def __getitem__(self, key):
                self.accessed.append(key)
                return "v"

            def __iter__(self):
                for index in range(MAX_STRUCTURE_PROPERTIES):
                    yield f"key-{index}"
                raise RuntimeError("failing 257th property")

            def __len__(self):
                raise AssertionError("len must not be called")

        attrs = FailingLookaheadMapping()
        snapshot, reasons = flatten_and_prune_context(attrs)
        assert len(snapshot) == MAX_STRUCTURE_PROPERTIES
        assert reasons == frozenset(
            (CONTEXT_TRUNCATION_MAX_CONTEXT_FIELDS, CONTEXT_TRUNCATION_MAX_STRUCTURE_PROPERTIES)
        )
        assert attrs.accessed == [f"key-{index}" for index in range(MAX_STRUCTURE_PROPERTIES)]

    def test_sequence_failing_width_lookahead_preserves_prefix_and_stops(self):
        class FailingLookaheadSequence(Sequence):
            def __init__(self):
                self.accessed = []

            def __getitem__(self, index):
                self.accessed.append(index)
                if index < MAX_LIST_ELEMENTS:
                    return "v"
                raise RuntimeError("failing 257th element")

            def __len__(self):
                raise AssertionError("len must not be called")

        values = FailingLookaheadSequence()
        snapshot, reasons = flatten_and_prune_context({"values": values})
        assert len(snapshot) == MAX_LIST_ELEMENTS
        assert CONTEXT_TRUNCATION_MAX_LIST_ELEMENTS in reasons
        assert values.accessed == list(range(MAX_LIST_ELEMENTS + 1))

    def test_non_string_scalars_do_not_get_an_unapproved_length_cap(self, writer):
        large_integer = 10 ** (MAX_VALUE_LENGTH + 1)
        writer.enqueue(_make_event(attrs={"value": large_integer, "missing": None}))
        # The null is omitted, and omitting it is not a truncation.
        assert writer._queue.get_nowait().attrs == {"value": large_integer}
        assert writer._context_truncated == {}

    @pytest.mark.parametrize(
        "attrs",
        [
            pytest.param({"kept": "retained", "unsafe": b"x" * (MAX_VALUE_LENGTH + 1)}, id="bytes-value"),
            pytest.param({"kept": "retained", "unsafe": object()}, id="opaque-value"),
            pytest.param({"kept": "retained", 2: "dropped"}, id="non-string-key"),
            pytest.param({"kept": "retained", "unsafe": _UnsafeLeaf()}, id="leaf-refusing-str"),
        ],
    )
    def test_unsupported_fields_drop_only_themselves(self, writer, attrs):
        """One unsupported field must not discard the valid fields around it.

        The leaf-refusing-str case also pins that the walker never calls str() on a
        caller object to coerce it. Ruby's bounded_context_snapshot falls through its
        leaf type case the same way, so Python matches the reference implementation.
        """
        writer.enqueue(_make_event(attrs=attrs))
        assert writer._queue.get_nowait().attrs == {"kept": "retained"}
        assert writer._context_truncated == {CONTEXT_TRUNCATION_UNSUPPORTED_VALUE: 1}

    def test_scalar_subclasses_are_retained_without_running_caller_hooks(self, writer):
        """IntEnum, StrEnum, and str-subclass keys are common in real context."""

        class Tier(enum.IntEnum):
            GOLD = 3

        class Region(str, enum.Enum):
            EU = "eu-west"

            def __str__(self):
                raise AssertionError("subclass conversion must not run")

        class LazyKey(str):
            def __str__(self):
                raise AssertionError("subclass conversion must not run")

        writer.enqueue(_make_event(attrs={"tier": Tier.GOLD, "region": Region.EU, LazyKey("lazy"): "v"}))
        assert writer._queue.get_nowait().attrs == {"tier": 3, "region": "eu-west", "lazy": "v"}
        assert writer._context_truncated == {}

    def test_empty_keys_do_not_alias_distinct_contexts(self):
        """An empty caller key must keep its path segment.

        Only the root mapping omits the "." separator. If the separator decision keyed
        off len(prefix) instead, a nested container with an empty prefix would reclaim
        the separator and two distinct contexts would flatten to the same key set, then
        merge into one aggregation bucket.
        """
        nested_under_empty_key, _ = flatten_and_prune_context({"": {"a": 1}})
        top_level, _ = flatten_and_prune_context({"a": 1})
        assert nested_under_empty_key == {".a": 1}
        assert top_level == {"a": 1}
        assert canonical_context_key(nested_under_empty_key) != canonical_context_key(top_level)

    def test_empty_keys_do_not_alias_at_depth(self):
        """The same aliasing must not reappear one level down."""
        with_empty_segment, _ = flatten_and_prune_context({"a": {"": {"b": 1}}})
        without, _ = flatten_and_prune_context({"a": {"b": 1}})
        assert with_empty_segment == {"a..b": 1}
        assert without == {"a.b": 1}
        assert canonical_context_key(with_empty_segment) != canonical_context_key(without)


    def test_string_value_limit_counts_characters(self):
        exact_snapshot, exact_reasons = flatten_and_prune_context({"value": "😀" * MAX_VALUE_LENGTH})
        truncated_snapshot, truncated_reasons = flatten_and_prune_context({"value": "😀" * (MAX_VALUE_LENGTH + 1)})
        assert exact_snapshot == {"value": "😀" * MAX_VALUE_LENGTH}
        assert exact_reasons == frozenset()
        assert truncated_snapshot == {}
        assert truncated_reasons == frozenset((CONTEXT_TRUNCATION_MAX_VALUE_LENGTH,))

    def test_datetime_subclass_isoformat_is_not_called(self, writer):
        class UnsafeDatetime(datetime):
            def isoformat(self, *args, **kwargs):
                raise AssertionError("subclass conversion must not run")

        writer.enqueue(_make_event(attrs={"value": UnsafeDatetime(2026, 1, 1), "kept": "retained"}))
        # datetime.isoformat is called unbound, so the subclass override never runs
        # and the value is retained normally.
        assert writer._queue.get_nowait().attrs == {
            "value": "2026-01-01T00:00:00",
            "kept": "retained",
        }
        assert writer._context_truncated == {}


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

    def test_pre_queue_json_conversion_preserves_other_vs_string_bucket_distinction(self, writer):
        timestamp = datetime(2026, 6, 23, 12, 30, tzinfo=timezone.utc)
        writer.enqueue(_make_event(attrs={"x": timestamp}))
        writer.enqueue(_make_event(attrs={"x": timestamp.isoformat()}))

        writer._drain_queue()

        assert len(writer._full) == 2
        assert {entry.context_attrs["x"] for entry in writer._full.values()} == {timestamp.isoformat()}

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
        with pytest.raises(TypeError):
            queued.attrs["new"] = "value"

    def test_enqueue_flattens_nested_context_snapshot(self, writer):
        writer.enqueue(_make_event(attrs={"user": {"id": 123, "plan": "pro"}}))

        queued = writer._queue.get_nowait()
        assert queued.attrs == {"user.id": 123, "user.plan": "pro"}

    def test_snapshot_error_queues_empty_context_and_is_counted(self, writer):
        attrs = mock.MagicMock()
        attrs.__bool__.return_value = True
        attrs.items.side_effect = RuntimeError("cannot iterate")

        writer.enqueue(_make_event(attrs=attrs))

        assert writer._queue.get_nowait().attrs == {}
        assert writer._context_truncated == {CONTEXT_TRUNCATION_SNAPSHOT_ERROR: 1}

    def test_snapshot_error_log_excludes_caller_context_data(self, writer, caplog):
        """A caller-controlled exception message must not reach the log sink.

        The traversal calls __iter__ and __getitem__ on the caller's own object, so
        the exception message and traceback can carry customer context. That data is
        consent-gated in the payload and must not leak to logs.
        """
        secret = "SSN=123-45-6789"

        class RaisingIterMapping(Mapping):
            """A caller mapping whose iteration raises with sensitive text."""

            def __iter__(self):
                raise RuntimeError(secret)

            def __getitem__(self, key):
                raise KeyError(key)

            def __len__(self):
                return 1

        with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.openfeature._flagevaluation_writer"):
            writer.enqueue(_make_event(attrs=RaisingIterMapping()))

        assert writer._context_truncated == {CONTEXT_TRUNCATION_SNAPSHOT_ERROR: 1}
        assert caplog.records, "expected one snapshot-error log record"
        for record in caplog.records:
            assert secret not in record.getMessage()
            assert record.exc_info is None
        assert "RuntimeError" in caplog.records[0].getMessage()

    def test_canonicalization_error_log_excludes_caller_context_data(self, writer, caplog):
        """The drain-thread canonicalization failure must not log context values."""
        secret = "email=alice@corp.com"

        with mock.patch(
            "ddtrace.internal.openfeature._flagevaluation_writer.canonical_context_key",
            side_effect=ValueError(secret),
        ):
            with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.openfeature._flagevaluation_writer"):
                writer._aggregate(_make_event(attrs={"user": "u1"}))

        assert caplog.records, "expected one canonicalization-error log record"
        for record in caplog.records:
            assert secret not in record.getMessage()
            assert record.exc_info is None
        assert "ValueError" in caplog.records[0].getMessage()

    def test_enqueue_after_shutdown_started_is_dropped_and_counted(self, writer):
        writer.on_shutdown()

        with mock.patch(TELEMETRY_COUNT_PATCH) as mock_count:
            writer.enqueue(_make_event(flag_key="late"))

        assert writer._queue.qsize() == 0
        _assert_count_metric(mock_count, FLAG_EVALUATION_DROPPED_METRIC, 1, FLAG_EVALUATION_REASON_CLOSED)


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

    def test_unsupported_context_value_emits_row_with_empty_context(self, writer):
        writer.enqueue(_make_event(attrs={"unsupported": object()}))

        with mock.patch.object(writer, "_send_payload") as mock_send:
            writer.periodic()

        row = json.loads(mock_send.call_args[0][0])["flagEvaluations"][0]
        assert "context" not in row

    def test_unencodable_integer_context_does_not_abort_drain(self, writer):
        writer.enqueue(_make_event(flag_key="huge-int", attrs={"value": 10**5000}))
        writer.enqueue(_make_event(flag_key="following", attrs={"value": "kept"}))

        with mock.patch(TELEMETRY_COUNT_PATCH) as mock_count:
            with mock.patch.object(writer, "_send_payload") as mock_send:
                writer.periodic()

        rows = {row["flag"]["key"]: row for row in json.loads(mock_send.call_args[0][0])["flagEvaluations"]}
        assert "context" not in rows["huge-int"]
        assert rows["following"]["context"]["evaluation"] == {"value": "kept"}
        _assert_count_metric(
            mock_count,
            FLAG_EVALUATION_CONTEXT_TRUNCATED_METRIC,
            1,
            CONTEXT_TRUNCATION_SNAPSHOT_ERROR,
        )

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

    def test_serialization_failure_drops_only_bad_row_and_counts_exact_evaluations(self, writer):
        writer.enqueue(_make_event(flag_key="invalid"))
        writer.enqueue(_make_event(flag_key="invalid"))
        writer.enqueue(_make_event(flag_key="valid"))

        with mock.patch(
            "ddtrace.internal.openfeature._flagevaluation_writer._json_dumps",
            side_effect=_json_dumps_rejecting_invalid_row,
        ):
            with mock.patch(TELEMETRY_COUNT_PATCH) as mock_count:
                with mock.patch.object(writer, "_send_payload") as mock_send:
                    writer.periodic()

        mock_send.assert_called_once()
        rows = json.loads(mock_send.call_args[0][0])["flagEvaluations"]
        assert [row["flag"]["key"] for row in rows] == ["valid"]
        serialization_calls = [
            call
            for call in mock_count.call_args_list
            if call.args[1] == FLAG_EVALUATION_DROPPED_METRIC
            and call.args[3] == (("reason", FLAG_EVALUATION_REASON_SERIALIZATION_ERROR),)
        ]
        assert serialization_calls == [
            mock.call(
                TELEMETRY_NAMESPACE.TRACERS,
                FLAG_EVALUATION_DROPPED_METRIC,
                2,
                (("reason", FLAG_EVALUATION_REASON_SERIALIZATION_ERROR),),
            )
        ]
        _assert_no_count_metric(
            mock_count,
            FLAG_EVALUATION_DROPPED_METRIC,
            FLAG_EVALUATION_REASON_PAYLOAD_LIMIT,
        )

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
        assert result.dropped_serialization_error == 0
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
        assert result.dropped_serialization_error == 0
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


# ---------------------------------------------------------------------------
# Shutdown drains the queue + final-flush before exit
# ---------------------------------------------------------------------------


class TestShutdownDrain:
    def test_direct_shutdown_without_worker_drains_and_flushes(self, writer):
        writer.enqueue(_make_event(flag_key="direct-shutdown"))

        with mock.patch.object(writer, "_send_payload") as mock_send:
            writer.on_shutdown()

        mock_send.assert_called_once()
        row = json.loads(mock_send.call_args[0][0])["flagEvaluations"][0]
        assert row["flag"]["key"] == "direct-shutdown"
        assert writer._queue.empty()

    def test_on_shutdown_signals_drain_worker_without_joining(self, writer):
        drain_worker = mock.Mock()
        writer._drain_worker = drain_worker

        with mock.patch.object(writer, "periodic") as mock_periodic:
            writer.on_shutdown()

        drain_worker.stop.assert_called_once()
        drain_worker.join.assert_not_called()
        mock_periodic.assert_called_once()
        assert writer._accepting_events is False

    def test_shutdown_flushes_pending_event_with_stopped_drain_worker_reference(self, writer):
        drain_worker = PeriodicThread(
            3600.0,
            target=writer._drain_queue,
            name="ffl3060-stopped-drain",
            on_shutdown=writer.periodic,
            no_wait_at_start=False,
        )
        drain_worker.start()
        drain_worker.stop()
        drain_worker.join(timeout=2.0)
        writer._drain_worker = drain_worker
        writer.enqueue(_make_event(flag_key="stopped-drain"))

        with mock.patch.object(writer, "_send_payload") as mock_send:
            writer.on_shutdown()

        mock_send.assert_called_once()
        rows = json.loads(mock_send.call_args[0][0])["flagEvaluations"]
        assert len(rows) == 1
        assert rows[0]["flag"]["key"] == "stopped-drain"
        assert writer._queue.empty()

    def test_start_failure_closes_intake_and_signals_drain_worker(self, writer):
        drain_worker = mock.Mock()
        with mock.patch(
            "ddtrace.internal.openfeature._flagevaluation_writer.PeriodicThread",
            return_value=drain_worker,
        ):
            with mock.patch(
                "ddtrace.internal.periodic.PeriodicService._start_service",
                side_effect=RuntimeError("startup failed"),
            ):
                with pytest.raises(RuntimeError, match="startup failed"):
                    writer.start()

        drain_worker.start.assert_called_once()
        drain_worker.stop.assert_called_once()
        drain_worker.join.assert_not_called()
        assert writer._accepting_events is False
        with mock.patch(TELEMETRY_COUNT_PATCH) as mock_count:
            writer.enqueue(_make_event(flag_key="after-failed-start"))
        _assert_count_metric(mock_count, FLAG_EVALUATION_DROPPED_METRIC, 1, FLAG_EVALUATION_REASON_CLOSED)
        assert writer._queue.empty()

    def test_real_start_stop_lifecycle_drains_pending_events(self):
        """The drain worker owns the final drain and flush during service shutdown."""
        writer = FlagEvaluationWriter(interval=3600.0)
        sent = []
        with mock.patch.object(writer, "_send_payload", side_effect=lambda p, n: sent.append((p, n))):
            writer.start()
            writer.enqueue(_make_event(flag_key="pending-1"))
            writer.enqueue(_make_event(flag_key="pending-2"))
            writer.stop()
            writer.join(timeout=5.0)

        assert len(sent) == 1, "stop() must trigger a final drain and flush"
        decoded = json.loads(sent[0][0])
        flags = {row["flag"]["key"] for row in decoded["flagEvaluations"]}
        assert flags == {"pending-1", "pending-2"}
        assert writer._queue.empty()

    def test_stop_drain_worker_bounds_the_join(self, writer):
        """The shutdown join must pass a timeout so a stalled worker cannot hang exit."""
        worker = mock.Mock()
        writer._drain_worker = worker

        writer._stop_drain_worker()

        worker.stop.assert_called_once()
        worker.join.assert_called_once_with(timeout=DRAIN_WORKER_JOIN_TIMEOUT)
        assert writer._drain_worker is worker

    def test_final_drain_emits_accepted_truncation_counter(self):
        writer = FlagEvaluationWriter(interval=3600.0)
        with mock.patch(TELEMETRY_COUNT_PATCH) as mock_count:
            with mock.patch.object(writer, "_send_payload"):
                writer.start()
                writer.enqueue(_make_event(attrs={"value": "v" * (MAX_VALUE_LENGTH + 1)}))
                writer.stop()
                writer.join(timeout=5.0)
        _assert_count_metric(
            mock_count,
            FLAG_EVALUATION_CONTEXT_TRUNCATED_METRIC,
            1,
            CONTEXT_TRUNCATION_MAX_VALUE_LENGTH,
        )

    def test_shutdown_rejects_snapshot_that_finishes_after_final_drain(self, writer):
        snapshot_started = threading.Event()
        release_snapshot = threading.Event()

        class BlockingMapping(Mapping):
            def __getitem__(self, key):
                return "value"

            def __iter__(self):
                snapshot_started.set()
                assert release_snapshot.wait(2.0)
                yield "key"

            def __len__(self):
                raise AssertionError("len must not be called")

        enqueue_thread = threading.Thread(target=writer.enqueue, args=(_make_event(attrs=BlockingMapping()),))
        enqueue_thread.start()
        assert snapshot_started.wait(2.0)

        with mock.patch(TELEMETRY_COUNT_PATCH) as mock_count:
            writer.on_shutdown()
            release_snapshot.set()
            enqueue_thread.join(2.0)

        assert not enqueue_thread.is_alive()
        assert writer._queue.empty()
        _assert_count_metric(mock_count, FLAG_EVALUATION_DROPPED_METRIC, 1, FLAG_EVALUATION_REASON_CLOSED)

    def test_shutdown_waits_for_dequeued_event_before_final_flush(self):
        writer = FlagEvaluationWriter(interval=3600.0)
        aggregate_started = threading.Event()
        release_aggregate = threading.Event()
        shutdown_finished = threading.Event()
        original_aggregate = writer._aggregate

        def blocking_aggregate(event: _EvalEvent) -> None:
            aggregate_started.set()
            assert release_aggregate.wait(3.0)
            original_aggregate(event)

        sent = []
        with mock.patch.object(writer, "_aggregate", side_effect=blocking_aggregate):
            with mock.patch.object(
                writer, "_send_payload", side_effect=lambda payload, count: sent.append((payload, count))
            ):
                writer.start()
                writer.enqueue(_make_event(flag_key="already-dequeued"))
                assert aggregate_started.wait(2.0)

                shutdown_thread = threading.Thread(
                    target=lambda: (writer.stop(), writer.join(timeout=3.0), shutdown_finished.set())
                )
                shutdown_thread.start()
                try:
                    assert not shutdown_finished.wait(1.1)
                finally:
                    release_aggregate.set()
                shutdown_thread.join(3.0)

        assert not shutdown_thread.is_alive()
        assert len(sent) == 1
        row = json.loads(sent[0][0])["flagEvaluations"][0]
        assert row["flag"]["key"] == "already-dequeued"
        assert row["evaluation_count"] == 1

    def test_drain_worker_final_flushes_dequeued_event_after_join_timeout(self):
        writer = FlagEvaluationWriter(interval=3600.0)
        aggregate_started = threading.Event()
        release_aggregate = threading.Event()
        original_aggregate = writer._aggregate

        def blocking_aggregate(event: _EvalEvent) -> None:
            aggregate_started.set()
            assert release_aggregate.wait(3.0)
            original_aggregate(event)

        sent = []
        with mock.patch(
            "ddtrace.internal.openfeature._flagevaluation_writer.DRAIN_WORKER_JOIN_TIMEOUT",
            0.01,
        ):
            with mock.patch.object(writer, "_aggregate", side_effect=blocking_aggregate):
                with mock.patch.object(
                    writer,
                    "_send_payload",
                    side_effect=lambda payload, count: sent.append((payload, count)),
                ):
                    writer.start()
                    drain_worker = writer._drain_worker
                    assert drain_worker is not None
                    writer.enqueue(_make_event(flag_key="already-dequeued"))
                    assert aggregate_started.wait(2.0)

                    try:
                        started = time.monotonic()
                        writer.stop()
                        writer.join(timeout=1.0)
                        assert time.monotonic() - started < 1.0
                        assert sent == []
                    finally:
                        release_aggregate.set()
                        drain_worker.join(timeout=3.0)

        assert len(sent) == 1
        rows = json.loads(sent[0][0])["flagEvaluations"]
        assert len(rows) == 1
        assert rows[0]["flag"]["key"] == "already-dequeued"
        assert rows[0]["evaluation_count"] == 1

    @pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork")
    def test_fork_child_resets_inherited_queue_lock_and_events(self, writer):
        writer.enqueue(_make_event(flag_key="parent-only"))
        parent_queue = writer._queue
        read_fd, write_fd = os.pipe()
        child_pid = -1
        parent_queue.mutex.acquire()
        try:
            child_pid = os.fork()
            if child_pid == 0:
                try:
                    os.close(read_fd)
                    writer.enqueue(_make_event(flag_key="child-only"))
                    child_event = writer._queue.get_nowait()
                    os.write(write_fd, f"{child_event.flag_key}:{writer._queue.qsize()}".encode())
                finally:
                    os._exit(0)
        finally:
            parent_queue.mutex.release()
            os.close(write_fd)

        try:
            readable, _, _ = select.select([read_fd], [], [], 3.0)
            assert readable, "fork child blocked on an inherited queue lock"
            assert os.read(read_fd, 128) == b"child-only:0"
            _, status = os.waitpid(child_pid, 0)
            assert os.waitstatus_to_exitcode(status) == 0
        finally:
            os.close(read_fd)
            if child_pid > 0:
                try:
                    waited, _ = os.waitpid(child_pid, os.WNOHANG)
                except ChildProcessError:
                    waited = child_pid
                if waited == 0:
                    os.kill(child_pid, 9)
                    os.waitpid(child_pid, 0)

        assert writer._queue is parent_queue
        assert writer._queue.get_nowait().flag_key == "parent-only"

    def test_fork_child_reopens_intake_closed_by_parent_shutdown(self, writer):
        """A child forked after the parent closed its intake must still accept events.

        The child restarts its PeriodicThread through threads._after_fork_child, which
        never calls _start_service. An inherited accepting_events of False would
        therefore close the child intake for the life of the process.
        """
        with mock.patch.object(writer, "_send_payload"):
            writer.on_shutdown()
        assert writer._accepting_events is False, "parent intake must be closed"

        read_fd, write_fd = os.pipe()
        child_pid = -1
        try:
            child_pid = os.fork()
            if child_pid == 0:
                try:
                    os.close(read_fd)
                    writer.enqueue(_make_event(flag_key="child-after-parent-shutdown"))
                    os.write(write_fd, f"{writer._accepting_events}:{writer._queue.qsize()}".encode())
                finally:
                    os._exit(0)
        finally:
            os.close(write_fd)

        try:
            readable, _, _ = select.select([read_fd], [], [], 3.0)
            assert readable, "fork child produced no result"
            assert os.read(read_fd, 128) == b"True:1"
            _, status = os.waitpid(child_pid, 0)
            assert os.waitstatus_to_exitcode(status) == 0
        finally:
            os.close(read_fd)
            if child_pid > 0:
                try:
                    waited, _ = os.waitpid(child_pid, os.WNOHANG)
                except ChildProcessError:
                    waited = child_pid
                if waited == 0:
                    os.kill(child_pid, 9)
                    os.waitpid(child_pid, 0)

        # The parent intake stays closed. The child reset must not leak back.
        assert writer._accepting_events is False

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
                            with w._counter_lock:
                                assert w._dropped_queue == 0
                            with w._lock:
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
    def test_pre_queue_counter_does_not_wait_for_aggregation_lock(self, writer):
        writer._queue = queue.Queue(maxsize=1)
        writer._queue.put_nowait(_make_event(flag_key="queued"))
        finished = threading.Event()

        with writer._lock:
            enqueue_thread = threading.Thread(
                target=lambda: (writer.enqueue(_make_event(flag_key="dropped")), finished.set())
            )
            enqueue_thread.start()
            assert finished.wait(2.0)

        enqueue_thread.join(2.0)
        assert writer._dropped_pre_queue == 1

    def test_pre_queue_overflow_drop_count_is_emitted_and_reset(self, writer):
        writer._queue = queue.Queue(maxsize=1)
        writer._queue.put_nowait(_make_event(flag_key="queued"))

        # The O(1) full check must short-circuit before the snapshot walk, so an
        # overflowing caller never pays for traversing its own context.
        with mock.patch(
            "ddtrace.internal.openfeature._flagevaluation_writer.flatten_and_prune_context"
        ) as mock_snapshot:
            writer.enqueue(_make_event(flag_key="dropped"))

        mock_snapshot.assert_not_called()
        assert writer._dropped_pre_queue == 1
        assert writer._dropped_queue == 0

        with mock.patch(TELEMETRY_COUNT_PATCH) as mock_count:
            with mock.patch.object(writer, "_send_payload"):
                writer.periodic()

        _assert_count_metric(
            mock_count,
            FLAG_EVALUATION_DROPPED_METRIC,
            1,
            FLAG_EVALUATION_REASON_PRE_QUEUE_OVERFLOW,
        )
        _assert_no_count_metric(mock_count, FLAG_EVALUATION_DROPPED_METRIC, FLAG_EVALUATION_REASON_QUEUE_OVERFLOW)
        assert writer._dropped_pre_queue == 0

    def test_send_site_queue_race_is_emitted_and_reset_without_aggregate_rows(self, writer):
        """A put_nowait losing the capacity race counts separately from the precheck."""
        with mock.patch.object(writer._queue, "full", return_value=False):
            with mock.patch.object(writer._queue, "put_nowait", side_effect=queue.Full):
                writer.enqueue(
                    _make_event(
                        flag_key="race",
                        attrs={"value": "v" * (MAX_VALUE_LENGTH + 1)},
                    )
                )

        assert writer._dropped_pre_queue == 0
        assert writer._dropped_queue == 1
        # The snapshot ran before the race, so its truncation must survive the drop.
        assert writer._context_truncated == {CONTEXT_TRUNCATION_MAX_VALUE_LENGTH: 1}

        with mock.patch(TELEMETRY_COUNT_PATCH) as mock_count:
            writer.periodic()

        _assert_count_metric(mock_count, FLAG_EVALUATION_DROPPED_METRIC, 1, FLAG_EVALUATION_REASON_QUEUE_OVERFLOW)
        _assert_count_metric(
            mock_count,
            FLAG_EVALUATION_CONTEXT_TRUNCATED_METRIC,
            1,
            CONTEXT_TRUNCATION_MAX_VALUE_LENGTH,
        )
        _assert_no_count_metric(
            mock_count,
            FLAG_EVALUATION_DROPPED_METRIC,
            FLAG_EVALUATION_REASON_PRE_QUEUE_OVERFLOW,
        )
        assert writer._dropped_queue == 0
        assert writer._context_truncated == {}

    def test_context_truncation_counts_once_per_event_per_reason_and_resets_without_rows(self, writer):
        attrs = {
            "long-value": "v" * (MAX_VALUE_LENGTH + 1),
            "k" * (MAX_KEY_LENGTH + 1): "value",
        }
        writer.enqueue(_make_event(attrs=attrs))
        writer.enqueue(_make_event(attrs=attrs))
        writer._queue.get_nowait()
        writer._queue.get_nowait()

        with mock.patch(TELEMETRY_COUNT_PATCH) as mock_count:
            writer.periodic()

        _assert_count_metric(
            mock_count,
            FLAG_EVALUATION_CONTEXT_TRUNCATED_METRIC,
            2,
            CONTEXT_TRUNCATION_MAX_VALUE_LENGTH,
        )
        _assert_count_metric(
            mock_count,
            FLAG_EVALUATION_CONTEXT_TRUNCATED_METRIC,
            2,
            CONTEXT_TRUNCATION_MAX_KEY_LENGTH,
        )
        assert writer._context_truncated == {}

        with mock.patch(TELEMETRY_COUNT_PATCH) as next_flush_count:
            writer.periodic()
        _assert_no_count_metric(
            next_flush_count,
            FLAG_EVALUATION_CONTEXT_TRUNCATED_METRIC,
            CONTEXT_TRUNCATION_MAX_VALUE_LENGTH,
        )

    def test_all_context_truncation_reasons_are_emitted(self, writer):
        writer.enqueue(_make_event(flag_key="fields", attrs={str(i): "v" for i in range(MAX_CONTEXT_FIELDS + 1)}))
        writer.enqueue(_make_event(flag_key="key", attrs={"k" * (MAX_KEY_LENGTH + 1): "v"}))
        writer.enqueue(_make_event(flag_key="value", attrs={"value": "v" * (MAX_VALUE_LENGTH + 1)}))
        writer.enqueue(
            _make_event(
                flag_key="list",
                attrs={"values": ["v" * (MAX_VALUE_LENGTH + 1)] * (MAX_LIST_ELEMENTS + 1)},
            )
        )
        writer.enqueue(
            _make_event(
                flag_key="structure",
                attrs={"nested": {str(i): "v" * (MAX_VALUE_LENGTH + 1) for i in range(MAX_STRUCTURE_PROPERTIES + 1)}},
            )
        )
        writer.enqueue(_make_event(flag_key="depth", attrs={"a": {"b": {"c": {"d": {"e": {"f": "v"}}}}}}))
        cyclic = {}
        cyclic["self"] = cyclic
        writer.enqueue(_make_event(flag_key="cycle", attrs=cyclic))
        writer.enqueue(_make_event(flag_key="unsupported", attrs={"unsupported": object()}))

        # snapshot_error now covers only a genuine traversal fault, not a single
        # unsupported field.
        class RaisingMapping(Mapping):
            def __iter__(self):
                raise RuntimeError("traversal fault")

            def __getitem__(self, key):
                raise KeyError(key)

            def __len__(self):
                return 1

        writer.enqueue(_make_event(flag_key="error", attrs=RaisingMapping()))

        with mock.patch(TELEMETRY_COUNT_PATCH) as mock_count:
            with mock.patch.object(writer, "_send_payload"):
                writer.periodic()

        emitted_reasons = {
            call.args[3][0][1]
            for call in mock_count.call_args_list
            if call.args[1] == FLAG_EVALUATION_CONTEXT_TRUNCATED_METRIC
        }
        assert emitted_reasons == {
            CONTEXT_TRUNCATION_MAX_CONTEXT_FIELDS,
            CONTEXT_TRUNCATION_MAX_KEY_LENGTH,
            CONTEXT_TRUNCATION_MAX_VALUE_LENGTH,
            CONTEXT_TRUNCATION_MAX_LIST_ELEMENTS,
            CONTEXT_TRUNCATION_MAX_STRUCTURE_PROPERTIES,
            CONTEXT_TRUNCATION_MAX_SNAPSHOT_DEPTH,
            CONTEXT_TRUNCATION_CYCLE,
            CONTEXT_TRUNCATION_UNSUPPORTED_VALUE,
            CONTEXT_TRUNCATION_SNAPSHOT_ERROR,
        }

    def test_duplicate_truncation_reason_is_counted_once_per_event(self, writer):
        writer.enqueue(
            _make_event(
                attrs={
                    "first": "v" * (MAX_VALUE_LENGTH + 1),
                    "second": "v" * (MAX_VALUE_LENGTH + 1),
                }
            )
        )
        with mock.patch(TELEMETRY_COUNT_PATCH) as mock_count:
            writer.periodic()
        _assert_count_metric(
            mock_count,
            FLAG_EVALUATION_CONTEXT_TRUNCATED_METRIC,
            1,
            CONTEXT_TRUNCATION_MAX_VALUE_LENGTH,
        )

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

    def test_uncontended_aggregation_records_every_event_without_dropping(self, writer):
        """Well-under-cap aggregation must touch no drop counter.

        The conservation property under contention is covered by
        TestConcurrentEnqueue::test_every_concurrent_enqueue_is_either_buffered_or_counted.
        """
        writer._aggregate(_make_event(flag_key="a", attrs={"x": 1}))
        writer._aggregate(_make_event(flag_key="b", attrs={"x": 2}))
        writer._aggregate(_make_event(flag_key="a", attrs={"x": 1}))  # repeat -> count 2 on bucket a

        full_counts = sum(e.count for e in writer._full.values())
        assert full_counts == 3  # 2 (a) + 1 (b)
        assert writer._dropped_degraded_overflow == 0
        assert writer._dropped_pre_queue == 0
        assert writer._dropped_queue == 0


class TestConcurrentEnqueue:
    """enqueue runs on arbitrary caller hook threads across three separate locks."""

    def test_every_concurrent_enqueue_is_either_buffered_or_counted(self):
        """Under contention, each attempt must land in exactly one outcome.

        enqueue takes _lifecycle_lock and _counter_lock separately, and the O(1) full
        check is not atomic with put_nowait. A lost update in either counter, or a
        double count across the two drop paths, would make the drop telemetry
        disagree with what the caller actually attempted.
        """
        threads_count = 8
        per_thread = 250
        total_attempted = threads_count * per_thread

        # A queue far smaller than the offered load forces both drop paths to run:
        # the pre-queue full check and the put_nowait queue.Full race.
        with mock.patch("ddtrace.internal.openfeature._flagevaluation_writer.QUEUE_SIZE", 64):
            writer = FlagEvaluationWriter(interval=3600.0)
            assert writer._queue.maxsize == 64

            barrier = threading.Barrier(threads_count)
            errors: list[BaseException] = []

            def worker(thread_index: int) -> None:
                try:
                    barrier.wait(timeout=10.0)
                    for i in range(per_thread):
                        writer.enqueue(_make_event(flag_key=f"f{thread_index}", attrs={"i": i}))
                except BaseException as exc:  # noqa: BLE001 - surfaced via assert below
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(n,)) for n in range(threads_count)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30.0)
                assert not t.is_alive(), "enqueue must never block a caller thread"

            assert not errors, f"enqueue raised on a caller thread: {errors!r}"

            buffered = writer._queue.qsize()
            dropped_pre_queue = writer._dropped_pre_queue
            dropped_queue = writer._dropped_queue

        assert buffered + dropped_pre_queue + dropped_queue == total_attempted, (
            f"accounting lost or double-counted events: buffered={buffered} "
            f"pre_queue={dropped_pre_queue} queue={dropped_queue} attempted={total_attempted}"
        )
        # The load far exceeds the queue, so backpressure must have been exercised.
        assert dropped_pre_queue + dropped_queue > 0, "test did not exercise the drop paths"

    def test_concurrent_enqueue_and_flush_preserve_total_accounting(self):
        """A flush racing with enqueue must not lose or double-count events."""
        threads_count = 6
        per_thread = 200
        total_attempted = threads_count * per_thread

        writer = FlagEvaluationWriter(interval=3600.0)
        flushed = 0
        stop_flushing = threading.Event()
        barrier = threading.Barrier(threads_count + 1)
        errors: list[BaseException] = []

        def producer(thread_index: int) -> None:
            try:
                barrier.wait(timeout=10.0)
                for i in range(per_thread):
                    writer.enqueue(_make_event(flag_key=f"f{thread_index}", attrs={"i": i}))
            except BaseException as exc:  # noqa: BLE001 - surfaced via assert below
                errors.append(exc)

        def flusher() -> None:
            nonlocal flushed
            try:
                barrier.wait(timeout=10.0)
                while not stop_flushing.is_set():
                    # Drain and snapshot repeatedly, competing with the producers.
                    writer._drain_queue()
                    with writer._lock:
                        flushed += sum(e.count for e in writer._full.values())
                        flushed += sum(e.count for e in writer._degraded.values())
                        writer._full = {}
                        writer._degraded = {}
                        writer._per_flag_count = {}
                        writer._global_count = 0
            except BaseException as exc:  # noqa: BLE001 - surfaced via assert below
                errors.append(exc)

        producers = [threading.Thread(target=producer, args=(n,)) for n in range(threads_count)]
        flush_thread = threading.Thread(target=flusher)
        flush_thread.start()
        for t in producers:
            t.start()
        for t in producers:
            t.join(timeout=30.0)
            assert not t.is_alive(), "enqueue must never block a caller thread"
        stop_flushing.set()
        flush_thread.join(timeout=30.0)
        assert not flush_thread.is_alive()

        assert not errors, f"a thread raised: {errors!r}"

        # Sweep whatever the flusher did not observe before it stopped.
        writer._drain_queue()
        with writer._lock:
            residual = sum(e.count for e in writer._full.values())
            residual += sum(e.count for e in writer._degraded.values())

        accounted = flushed + residual + writer._dropped_pre_queue + writer._dropped_queue
        assert accounted == total_attempted, (
            f"accounting lost or double-counted events: flushed={flushed} residual={residual} "
            f"pre_queue={writer._dropped_pre_queue} queue={writer._dropped_queue} "
            f"attempted={total_attempted}"
        )
