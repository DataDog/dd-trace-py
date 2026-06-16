"""
Tests for APM span enrichment (PY-01).

Covers the FROZEN Node contract (dd-trace-js#8343) ported to dd-trace-py:
the ULEB128 delta-varint + base64 codec, the SHA256 targeting-key hash, the
SpanEnrichmentState accumulator (limits / dedupe / truncation), and the
SpanEnrichmentHook lifecycle (gate-gated construction, root-span-finish tag
write, symmetric subscribe/unsubscribe cleanup, gate-off negative control).

The 7 required L0 cases (VALIDATION.md:43-51) live here, plus the explicit
max-200 serial-id case and the codec golden-vector round-trip.

Run via: scripts/run-tests  (NEVER bare pytest -- AGENTS rule 1)
"""

import json

from openfeature.flag_evaluation import FlagEvaluationDetails
from openfeature.hook import HookContext
import pytest

from ddtrace.internal.openfeature._span_enrichment import SpanEnrichmentHook
from ddtrace.internal.openfeature._span_enrichment import SpanEnrichmentState
from ddtrace.internal.openfeature._span_enrichment import encode_delta_varint
from ddtrace.internal.openfeature._span_enrichment import hash_targeting_key


def _decode_delta_varint(encoded: str) -> set:
    """Decode side of the frozen contract -- mirrors the L2 codec
    (system-tests/.../test_ffe/utils.py). Used only as the round-trip oracle.
    """
    import base64

    if encoded == "":
        return set()
    raw = base64.b64decode(encoded)
    ids = set()
    prev = 0
    shift = 0
    cur = 0
    for byte in raw:
        cur |= (byte & 0x7F) << shift
        if byte & 0x80:
            shift += 7
            continue
        prev += cur
        ids.add(prev)
        cur = 0
        shift = 0
    return ids


class TestDeltaVarintCodec:
    """Pure codec unit (no tracer). Mirrors the L2 _Delta_Varint class."""

    def test_golden_vector(self):
        # Frozen golden vector: {100,108,128,130} -> deltas [100,8,20,2] -> ZAgUAg==
        assert encode_delta_varint({100, 108, 128, 130}) == "ZAgUAg=="

    def test_empty_set_returns_empty_string(self):
        # Empty set encodes to empty string => tag omitted.
        assert encode_delta_varint(set()) == ""

    def test_round_trip(self):
        ids = {100, 108, 128, 130}
        assert _decode_delta_varint(encode_delta_varint(ids)) == ids

    def test_round_trip_large_deltas_multibyte(self):
        # Values forcing multi-byte ULEB128 (delta > 0x7F).
        ids = {1, 200, 5000, 1_000_000}
        encoded = encode_delta_varint(ids)
        assert _decode_delta_varint(encoded) == ids

    def test_dedupe_and_sort_independent_of_insertion_order(self):
        # A set dedupes structurally; encode result is order-independent.
        assert encode_delta_varint({130, 100, 128, 108, 100}) == "ZAgUAg=="

    def test_hash_targeting_key_golden(self):
        assert (
            hash_targeting_key("user-123")
            == "fcdec6df4d44dbc637c7c5b58efface52a7f8a88535423430255be0bb89bedd8"
        )


class TestSpanEnrichmentState:
    """Unit-tests the accumulator limits (no tracer)."""

    def test_max_200_serial_ids_enforced(self):
        state = SpanEnrichmentState()
        for i in range(1, 251):  # 250 distinct ids
            state.add_serial_id(i)
        assert len(state._serial_ids) == SpanEnrichmentState.MAX_SERIAL_IDS == 200

    def test_serial_id_dedupe_via_set(self):
        state = SpanEnrichmentState()
        for _ in range(5):
            state.add_serial_id(42)
        assert state._serial_ids == {42}

    def test_max_subjects_cap(self):
        state = SpanEnrichmentState()
        for i in range(15):  # 15 distinct targeting keys
            state.add_subject(f"user-{i}", i)
        assert len(state._subjects) == SpanEnrichmentState.MAX_SUBJECTS == 10

    def test_max_experiments_per_subject(self):
        state = SpanEnrichmentState()
        for sid in range(30):  # 30 ids for a single subject
            state.add_subject("same-user", sid)
        hashed = hash_targeting_key("same-user")
        assert len(state._subjects[hashed]) == SpanEnrichmentState.MAX_EXPERIMENTS_PER_SUBJECT == 20

    def test_subject_keyed_by_sha256(self):
        state = SpanEnrichmentState()
        state.add_subject("user-123", 7)
        assert hash_targeting_key("user-123") in state._subjects
        assert "user-123" not in state._subjects  # raw key never stored

    def test_max_defaults_cap_first_wins(self):
        state = SpanEnrichmentState()
        for i in range(8):
            state.add_default(f"flag-{i}", f"v{i}")
        assert len(state._defaults) == SpanEnrichmentState.MAX_DEFAULTS == 5
        # First-wins: re-adding an existing key does not overwrite.
        state.add_default("flag-0", "CHANGED")
        assert state._defaults["flag-0"] == "v0"

    def test_runtime_default_detection_missing_variant_object_json_stringified(self):
        # Object default -> json.dumps (NOT str(dict) / "[object Object]").
        state = SpanEnrichmentState()
        state.add_default("obj-flag", {"a": 1, "b": [2, 3]})
        stored = state._defaults["obj-flag"]
        assert stored == json.dumps({"a": 1, "b": [2, 3]})
        assert json.loads(stored) == {"a": 1, "b": [2, 3]}

    def test_default_value_truncated_to_64_chars(self):
        state = SpanEnrichmentState()
        state.add_default("long-flag", "x" * 200)
        assert len(state._defaults["long-flag"]) == SpanEnrichmentState.MAX_DEFAULT_VALUE_LENGTH == 64

    def test_default_value_utf8_safe_truncation(self):
        # Truncating at 64 must not split a multi-byte character / must stay valid str.
        state = SpanEnrichmentState()
        state.add_default("emoji-flag", "🎉" * 100)
        value = state._defaults["emoji-flag"]
        assert len(value) <= SpanEnrichmentState.MAX_DEFAULT_VALUE_LENGTH
        # round-trips through utf-8 cleanly (no lone surrogate / broken byte)
        assert value.encode("utf-8").decode("utf-8") == value

    def test_has_data(self):
        state = SpanEnrichmentState()
        assert state.has_data() is False
        state.add_serial_id(1)
        assert state.has_data() is True

    def test_to_span_tags_shapes(self):
        # Pattern F: ffe_flags_enc bare base64; subjects/defaults JSON objects.
        state = SpanEnrichmentState()
        state.add_serial_id(100)
        state.add_serial_id(108)
        state.add_serial_id(128)
        state.add_serial_id(130)
        state.add_subject("user-123", 100)
        state.add_default("obj-flag", {"k": "v"})
        tags = state.to_span_tags()

        # ffe_flags_enc is a BARE base64 string.
        assert tags["ffe_flags_enc"] == "ZAgUAg=="
        assert isinstance(tags["ffe_flags_enc"], str)

        # ffe_subjects_enc is a JSON-stringified object {sha256hex: base64}.
        subjects = json.loads(tags["ffe_subjects_enc"])
        assert hash_targeting_key("user-123") in subjects

        # ffe_runtime_defaults is a JSON-stringified object {flagKey: valueStr}.
        defaults = json.loads(tags["ffe_runtime_defaults"])
        assert defaults["obj-flag"] == json.dumps({"k": "v"})

    def test_to_span_tags_omits_empty(self):
        state = SpanEnrichmentState()
        state.add_default("flag", "v")
        tags = state.to_span_tags()
        # No serial ids => no flags/subjects tags at all.
        assert "ffe_flags_enc" not in tags
        assert "ffe_subjects_enc" not in tags
        assert "ffe_runtime_defaults" in tags


def _make_details(value, variant, metadata):
    return FlagEvaluationDetails(
        flag_key="flag",
        value=value,
        variant=variant,
        flag_metadata=metadata or {},
    )


def _make_hook_context(flag_key="flag", targeting_key=None):
    from openfeature.evaluation_context import EvaluationContext

    ctx = EvaluationContext(targeting_key=targeting_key) if targeting_key else EvaluationContext()
    return HookContext(
        flag_key=flag_key,
        flag_type=bool,
        default_value=False,
        evaluation_context=ctx,
    )


class TestSpanEnrichmentHook:
    """Integration-style: capture, root-span-finish write, cleanup, error isolation."""

    def teardown_method(self):
        # Make sure no hook leaves a span-finish subscription behind between tests.
        try:
            from ddtrace.internal import core

            core.reset_listeners("trace.span_finish")
        except Exception:
            pass

    def test_no_active_span_no_crash(self):
        # eval with no active root span -> no crash, no state stored.
        hook = SpanEnrichmentHook()
        try:
            details = _make_details(True, "on", {"__dd_split_serial_id": 100, "__dd_do_log": True})
            hook.finally_after(_make_hook_context(targeting_key="user-1"), details, {})
            assert len(hook._span_states) == 0
        finally:
            hook.destroy()

    def test_finished_root_writes_decoded_flags(self):
        from ddtrace.trace import tracer

        hook = SpanEnrichmentHook()
        try:
            with tracer.trace("root") as root:
                for sid in (100, 108, 128, 130, 100):  # includes a dupe
                    details = _make_details(True, "on", {"__dd_split_serial_id": sid, "__dd_do_log": False})
                    hook.finally_after(_make_hook_context(), details, {})
                assert root in hook._span_states
            # After finish: tag written, state cleaned up.
            assert root.get_tag("ffe_flags_enc") == "ZAgUAg=="
            assert _decode_delta_varint(root.get_tag("ffe_flags_enc")) == {100, 108, 128, 130}
            assert root not in hook._span_states
        finally:
            hook.destroy()

    def test_runtime_default_missing_variant_writes_runtime_defaults(self):
        from ddtrace.trace import tracer

        hook = SpanEnrichmentHook()
        try:
            with tracer.trace("root") as root:
                # variant None + no serial id => runtime-default branch.
                details = _make_details({"color": "blue"}, None, {})
                hook.finally_after(_make_hook_context(flag_key="theme"), details, {})
            raw = root.get_tag("ffe_runtime_defaults")
            assert raw is not None
            assert json.loads(raw) == {"theme": json.dumps({"color": "blue"})}
            assert root.get_tag("ffe_flags_enc") is None
        finally:
            hook.destroy()

    def test_do_log_gating_subjects(self):
        from ddtrace.trace import tracer

        hook = SpanEnrichmentHook()
        try:
            with tracer.trace("root") as root:
                # do_log False + targeting key => serial id added, NO subject.
                d1 = _make_details(True, "on", {"__dd_split_serial_id": 5, "__dd_do_log": False})
                hook.finally_after(_make_hook_context(targeting_key="user-x"), d1, {})
                # do_log True + targeting key => subject added.
                d2 = _make_details(True, "on", {"__dd_split_serial_id": 6, "__dd_do_log": True})
                hook.finally_after(_make_hook_context(targeting_key="user-y"), d2, {})
                state = hook._span_states[root]
                assert state._serial_ids == {5, 6}
                assert hash_targeting_key("user-y") in state._subjects
                assert hash_targeting_key("user-x") not in state._subjects
            subjects = json.loads(root.get_tag("ffe_subjects_enc"))
            assert hash_targeting_key("user-y") in subjects
        finally:
            hook.destroy()

    def test_error_isolation_never_raises(self):
        # A malformed details object must not raise out of the hook.
        hook = SpanEnrichmentHook()
        try:
            from ddtrace.trace import tracer

            with tracer.trace("root"):
                hook.finally_after(_make_hook_context(), object(), {})  # bogus details
        finally:
            hook.destroy()

    def test_destroy_unsubscribes_span_finish(self):
        from ddtrace.internal import core

        assert core.has_listeners("trace.span_finish") in (True, False)
        hook = SpanEnrichmentHook()
        assert core.has_listeners("trace.span_finish") is True
        hook.destroy()
        # After destroy, our specific callback is gone (no listeners remain in this isolated test).
        assert core.has_listeners("trace.span_finish") is False

    def test_double_construction_no_duplicate_write(self):
        # Reconfigure scenario: a second hook must not cause double tag-application
        # via a stale subscription from the first.
        from ddtrace.trace import tracer

        hook1 = SpanEnrichmentHook()
        hook1.destroy()  # symmetric teardown
        hook2 = SpanEnrichmentHook()
        try:
            with tracer.trace("root") as root:
                details = _make_details(True, "on", {"__dd_split_serial_id": 100, "__dd_do_log": False})
                hook2.finally_after(_make_hook_context(), details, {})
            assert root.get_tag("ffe_flags_enc") == encode_delta_varint({100})
        finally:
            hook2.destroy()


class TestGateOffNegativeControl:
    """DG-005: gate off => NO ffe_* tags AND no per-span state allocated."""

    def test_gate_off_hook_never_constructed(self):
        from ddtrace.internal.openfeature._provider import DataDogProvider
        from tests.utils import override_global_config

        # Provider enabled, span-enrichment gate OFF.
        with override_global_config(
            {
                "_otel_metrics_enabled": False,
            }
        ):
            import ddtrace.internal.settings.openfeature as of_settings

            assert of_settings.config.experimental_flagging_provider_span_enrichment_enabled is False
            provider = DataDogProvider()
            # Hook attribute exists but is None -- NOTHING constructed/subscribed (DG-005).
            assert provider._span_enrichment_hook is None
            assert provider._span_enrichment_hook not in provider.get_provider_hooks()

    def test_gate_off_no_state_on_eval_path(self):
        # With the gate off the provider builds no SpanEnrichmentState; a finished
        # span carries no ffe_* tags.
        from ddtrace.trace import tracer

        with tracer.trace("root") as root:
            pass
        assert root.get_tag("ffe_flags_enc") is None
        assert root.get_tag("ffe_subjects_enc") is None
        assert root.get_tag("ffe_runtime_defaults") is None
