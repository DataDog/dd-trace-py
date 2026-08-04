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
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config
from tests.openfeature.config_helpers import create_string_flag


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
        assert hash_targeting_key("user-123") == "fcdec6df4d44dbc637c7c5b58efface52a7f8a88535423430255be0bb89bedd8"


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
        # Object default -> JSON.stringify (NOT str(dict) / "[object Object]").
        # Node JSON.stringify is COMPACT (no spaces): {"a":1,"b":[2,3]}. The exact
        # bytes are spelled out so this test documents the wire format / catches a
        # regression to Python's space-after-colon json.dumps default.
        state = SpanEnrichmentState()
        state.add_default("obj-flag", {"a": 1, "b": [2, 3]})
        stored = state._defaults["obj-flag"]
        assert stored == '{"a":1,"b":[2,3]}'
        assert json.loads(stored) == {"a": 1, "b": [2, 3]}

    def test_default_value_null_renders_node_null(self):
        # WR-01 parity: Node String(null) -> "null". Python str(None) -> "None".
        # A found-but-default flag whose resolved value is None (FlagDisabled /
        # DefaultAllocationNull) MUST serialize "null" to match the Node wire byte.
        state = SpanEnrichmentState()
        state.add_default("null-flag", None)
        assert state._defaults["null-flag"] == "null"

    def test_default_value_bool_renders_node_lowercase(self):
        # WR-01 parity: Node String(true)/String(false) -> "true"/"false".
        # Python str(True)/str(False) -> "True"/"False" (capitalized) -- diverges.
        state = SpanEnrichmentState()
        state.add_default("bool-true", True)
        state.add_default("bool-false", False)
        assert state._defaults["bool-true"] == "true"
        assert state._defaults["bool-false"] == "false"

    def test_default_value_list_renders_compact_json(self):
        # WR-01 parity: array default -> compact JSON.stringify ([1,"a",true]),
        # NOT Python's str(list) ("[1, 'a', True]") and NOT space-padded json.
        state = SpanEnrichmentState()
        state.add_default("list-flag", [1, "a", True])
        assert state._defaults["list-flag"] == '[1,"a",true]'

    def test_default_value_nested_object_with_bool_and_null_compact(self):
        # Nested structures route through json.dumps, so inner bool/null already
        # render as JSON true/false/null -- verify the full compact byte string.
        state = SpanEnrichmentState()
        state.add_default("nested", {"on": True, "off": False, "x": None, "n": 3})
        assert state._defaults["nested"] == '{"on":true,"off":false,"x":null,"n":3}'

    def test_default_value_number_and_string_unchanged(self):
        # WR-01: numbers and strings already match Node's String(); leave as str().
        state = SpanEnrichmentState()
        state.add_default("int-flag", 42)
        state.add_default("float-flag", 3.5)
        state.add_default("str-flag", "hello")
        assert state._defaults["int-flag"] == "42"
        assert state._defaults["float-flag"] == "3.5"
        assert state._defaults["str-flag"] == "hello"

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

    def test_default_value_truncation_counts_utf16_units_like_node(self):
        # WR-02 parity: Node slice(0,64) counts UTF-16 CODE UNITS. Each astral
        # code point (e.g. "🎉" U+1F389) is a surrogate PAIR = 2 UTF-16 units, so
        # Node keeps 64/2 = 32 emoji. Python str[:64] would keep 64 code points
        # (64 emoji) -- a byte-for-byte divergence. After the fix we keep 32.
        state = SpanEnrichmentState()
        state.add_default("emoji-flag", "🎉" * 100)
        value = state._defaults["emoji-flag"]
        assert value == "🎉" * 32
        # 32 code points here, NOT 64 -- proves UTF-16-unit (not code-point) counting.
        assert len(value) == 32
        # The Node-equivalent reference: slice the UTF-16 encoding at 64 units.
        node_equiv = ("🎉" * 100).encode("utf-16-le")[: 64 * 2].decode("utf-16-le")
        assert value == node_equiv
        # Still valid UTF-8 (no lone surrogate left by the pair-aligned cut).
        assert value.encode("utf-8").decode("utf-8") == value

    def test_default_value_astral_under_codepoint_limit_still_truncated(self):
        # Regression (codex P2): 40 emoji == 40 Python code points (<=64) but 80
        # UTF-16 units (>64). A `len(value_str) > 64` guard would SKIP truncation
        # and emit all 40, while Node's slice(0,64) keeps 32 -- a wire divergence.
        # Truncation must run based on UTF-16 units, not the code-point len().
        state = SpanEnrichmentState()
        state.add_default("emoji-flag", "🎉" * 40)
        value = state._defaults["emoji-flag"]
        assert value == "🎉" * 32
        assert len(value) == 32

    def test_default_value_truncation_drops_split_surrogate_stays_valid_utf8(self):
        # WR-02 edge: when the 64-unit boundary falls in the MIDDLE of a surrogate
        # pair (a leading BMP char shifts the astral chars by one unit), the lone
        # high surrogate must be dropped so the result is still valid UTF-8.
        # "a" (1 unit) + "🎉"*100 (2 units each): unit 64 is the HIGH surrogate of
        # the 32nd emoji (units 2..63 are emoji 1..31's pairs; wait through it):
        #   unit 0      = "a"
        #   units 1..63 = 31 full emoji (62 units) + 1 leftover HIGH surrogate
        # => keep "a" + 31 emoji, drop the split surrogate.
        state = SpanEnrichmentState()
        state.add_default("mixed-flag", "a" + "🎉" * 100)
        value = state._defaults["mixed-flag"]
        assert value == "a" + "🎉" * 31
        # No lone surrogate -> round-trips through utf-8 cleanly.
        assert value.encode("utf-8").decode("utf-8") == value
        # Matches the Node slice(0,64) reference exactly.
        node_equiv = ("a" + "🎉" * 100).encode("utf-16-le")[: 64 * 2].decode("utf-16-le", "ignore")
        assert value == node_equiv

    def test_default_value_truncation_bmp_unchanged_at_64(self):
        # Regression guard: pure-BMP content (1 code point == 1 UTF-16 unit) keeps
        # exactly 64 chars -- the UTF-16 path must not change ASCII/BMP behavior.
        state = SpanEnrichmentState()
        state.add_default("ascii-flag", "x" * 200)
        assert state._defaults["ascii-flag"] == "x" * 64
        assert len(state._defaults["ascii-flag"]) == 64

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
        # The inner value string is compact JSON.stringify bytes ({"k":"v"}).
        defaults = json.loads(tags["ffe_runtime_defaults"])
        assert defaults["obj-flag"] == '{"k":"v"}'

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

    def setup_method(self):
        # Hooks constructed via self._hook() are tracked and torn down with their
        # own destroy() (targeted, by-callback) -- NOT a blanket
        # core.reset_listeners("trace.span_finish"), which would also remove any
        # other product's span-finish listeners and could mask a real lifecycle
        # bug (e.g. a hook that fails to unsubscribe). Each test still calls
        # hook.destroy() explicitly; this is a belt-and-suspenders safety net.
        self._hooks: list = []

    def teardown_method(self):
        for hook in self._hooks:
            try:
                hook.destroy()
            except Exception:
                pass

    def _hook(self):
        hook = SpanEnrichmentHook()
        self._hooks.append(hook)
        return hook

    def test_no_active_span_no_crash(self):
        # eval with no active root span -> no crash, no state stored.
        hook = self._hook()
        try:
            details = _make_details(True, "on", {"__dd_split_serial_id": 100, "__dd_do_log": True})
            hook.finally_after(_make_hook_context(targeting_key="user-1"), details, {})
            assert len(hook._span_states) == 0
        finally:
            hook.destroy()

    def test_finished_root_writes_decoded_flags(self):
        from ddtrace.trace import tracer

        hook = self._hook()
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

        hook = self._hook()
        try:
            with tracer.trace("root") as root:
                # variant None + no serial id => runtime-default branch.
                details = _make_details({"color": "blue"}, None, {})
                hook.finally_after(_make_hook_context(flag_key="theme"), details, {})
            raw = root.get_tag("ffe_runtime_defaults")
            assert raw is not None
            # Object default rendered via compact JSON.stringify ({"color":"blue"}).
            assert json.loads(raw) == {"theme": '{"color":"blue"}'}
            assert root.get_tag("ffe_flags_enc") is None
        finally:
            hook.destroy()

    def test_do_log_gating_subjects(self):
        from ddtrace.trace import tracer

        hook = self._hook()
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
        hook = self._hook()
        try:
            from ddtrace.trace import tracer

            with tracer.trace("root"):
                hook.finally_after(_make_hook_context(), object(), {})  # bogus details
        finally:
            hook.destroy()

    def test_construction_does_not_subscribe(self):
        # Leak regression (codex P2): constructing a hook must NOT register a
        # global trace.span_finish listener. A DataDogProvider built but never
        # used for a real evaluation -- e.g. the llmobs prompt manager's
        # DataDogProvider(initialization_timeout=0) that fails to initialize and
        # is discarded without shutdown() -- would otherwise leak a listener that
        # runs on every span finish for the life of the process.
        hook = self._hook()
        assert hook._subscribed is False
        hook.destroy()  # safe no-op when never subscribed

    def test_destroy_unsubscribes_span_finish(self):
        from ddtrace.internal import core
        from ddtrace.trace import tracer

        hook = self._hook()
        # Subscription is LAZY: construction alone does not subscribe.
        assert hook._subscribed is False
        # The first data-bearing evaluation subscribes.
        with tracer.trace("root"):
            hook.finally_after(
                _make_hook_context(),
                _make_details(True, "on", {"__dd_split_serial_id": 100, "__dd_do_log": False}),
                {},
            )
        assert hook._subscribed is True
        assert core.has_listeners("trace.span_finish") is True
        hook.destroy()
        # After destroy, our specific callback is gone (no listeners remain in this isolated test).
        assert core.has_listeners("trace.span_finish") is False
        assert hook._subscribed is False

    def test_double_construction_no_duplicate_write(self):
        # Reconfigure scenario: a second hook must not cause double tag-application
        # via a stale subscription from the first.
        from ddtrace.trace import tracer

        hook1 = self._hook()
        hook1.destroy()  # symmetric teardown
        hook2 = self._hook()
        try:
            with tracer.trace("root") as root:
                details = _make_details(True, "on", {"__dd_split_serial_id": 100, "__dd_do_log": False})
                hook2.finally_after(_make_hook_context(), details, {})
            assert root.get_tag("ffe_flags_enc") == encode_delta_varint({100})
        finally:
            hook2.destroy()

    def test_child_span_evaluation_aggregates_onto_local_root(self):
        # Contract: a flag evaluated INSIDE a child span (or async continuation)
        # must aggregate onto the LOCAL ROOT, not the child. _get_root_span uses
        # tracer.current_root_span(), so multiple child-span evals fold onto the
        # one root and the child carries no ffe_* tag.
        from ddtrace.trace import tracer

        hook = self._hook()
        try:
            with tracer.trace("root") as root:
                with tracer.trace("child-a") as child_a:
                    hook.finally_after(
                        _make_hook_context(),
                        _make_details(True, "on", {"__dd_split_serial_id": 100, "__dd_do_log": False}),
                        {},
                    )
                with tracer.trace("child-b") as child_b:
                    hook.finally_after(
                        _make_hook_context(),
                        _make_details(True, "on", {"__dd_split_serial_id": 108, "__dd_do_log": False}),
                        {},
                    )
                # State accumulates on the ROOT while inside children.
                assert root in hook._span_states
                assert child_a not in hook._span_states
                assert child_b not in hook._span_states
            # Both child-span evals folded onto the single root.
            assert root.get_tag("ffe_flags_enc") == encode_delta_varint({100, 108})
            assert _decode_delta_varint(root.get_tag("ffe_flags_enc")) == {100, 108}
            # Children carry NO ffe_* tags.
            assert child_a.get_tag("ffe_flags_enc") is None
            assert child_b.get_tag("ffe_flags_enc") is None
        finally:
            hook.destroy()

    def test_no_data_evaluation_creates_no_lingering_state(self):
        # WR/lifecycle: an evaluation with NEITHER a serial id NOR a runtime
        # default (e.g. an older UFC payload that has a variant but no serial_id)
        # must not allocate per-root state that lingers until span GC. State is
        # only created inside the serial/default branches, and any state is
        # always popped on root-span finish.
        from ddtrace.trace import tracer

        hook = self._hook()
        try:
            with tracer.trace("root") as root:
                # variant present (not a runtime default) + no serial id => neither branch.
                hook.finally_after(_make_hook_context(), _make_details(True, "on", {}), {})
                assert root not in hook._span_states  # nothing allocated
            # Nothing to write, nothing left over.
            assert root.get_tag("ffe_flags_enc") is None
            assert root not in hook._span_states
        finally:
            hook.destroy()

    def test_empty_state_popped_on_finish(self):
        # Even if empty state somehow exists (defensive), root-span finish must
        # pop it -- it must not survive to span GC.
        from ddtrace.trace import tracer

        hook = self._hook()
        try:
            with tracer.trace("root") as root:
                # Subscribe as finally_after would (subscription is lazy), then
                # force-create empty state directly (bypassing the branch guard).
                hook._ensure_subscribed()
                hook._get_or_create_state(root)
                assert root in hook._span_states
                assert hook._span_states[root].has_data() is False
            assert root not in hook._span_states  # popped on finish despite no data
        finally:
            hook.destroy()

    def test_two_live_hooks_do_not_stomp_each_other(self):
        # Listener-lifecycle: two concurrently-live hooks (e.g. two OpenFeature
        # domains/providers) must BOTH receive finish callbacks. The native event
        # hub keys listeners by name and REPLACES same-name registrations, so a
        # shared subscription name would let the second hook displace the first's
        # finish callback while the first keeps accumulating state (never written,
        # never cleaned). Per-instance names prevent the stomp.
        from ddtrace.trace import tracer

        hook1 = self._hook()
        hook2 = self._hook()
        try:
            # Distinct subscription names => no replacement in the hub.
            assert hook1._subscription_name != hook2._subscription_name
            # Each hook accumulates onto its OWN root span and writes its own tag.
            with tracer.trace("root-1") as root1:
                hook1.finally_after(
                    _make_hook_context(),
                    _make_details(True, "on", {"__dd_split_serial_id": 100, "__dd_do_log": False}),
                    {},
                )
            with tracer.trace("root-2") as root2:
                hook2.finally_after(
                    _make_hook_context(),
                    _make_details(True, "on", {"__dd_split_serial_id": 5, "__dd_do_log": False}),
                    {},
                )
            assert root1.get_tag("ffe_flags_enc") == encode_delta_varint({100})
            assert root2.get_tag("ffe_flags_enc") == encode_delta_varint({5})
        finally:
            hook1.destroy()
            hook2.destroy()

    def test_destroyed_hook_leaves_other_hook_intact(self):
        # Symmetric teardown: destroying one hook removes ONLY its callback; a
        # second live hook still fires on finish (no over-broad reset).
        from ddtrace.trace import tracer

        hook1 = self._hook()
        hook2 = self._hook()
        try:
            hook1.destroy()  # remove ONLY hook1's callback
            with tracer.trace("root") as root:
                hook2.finally_after(
                    _make_hook_context(),
                    _make_details(True, "on", {"__dd_split_serial_id": 7, "__dd_do_log": False}),
                    {},
                )
            # hook2 still wired -> tag written.
            assert root.get_tag("ffe_flags_enc") == encode_delta_varint({7})
        finally:
            hook2.destroy()

    def test_concurrent_evaluations_on_one_root_no_lost_serial_ids(self):
        # Concurrency (state-level): many threads concurrently accumulate
        # distinct serial ids / subjects onto the SAME SpanEnrichmentState while
        # another thread snapshots+encodes it. The per-state lock must let every
        # distinct id land (no lost update under concurrent membership checks)
        # and to_span_tags() must take a consistent snapshot (no "set/dict
        # changed size during iteration"). The accumulator is the shared mutable
        # structure (one per root span) the lock is added to protect.
        #
        # This drives the accumulator directly rather than via finally_after,
        # because ddtrace's active-span context is thread-local: a worker thread
        # does NOT see the main thread's root span, so finally_after would no-op
        # off-thread. The real cross-thread contention the lock guards is the
        # shared SpanEnrichmentState, which is exercised here.
        import threading

        n_threads = 8
        per_thread = 25  # 8*25 = 200 == MAX_SERIAL_IDS (exactly fills, no drop)
        state = SpanEnrichmentState()
        errors: list = []
        stop_encoding = threading.Event()

        def add_ids(base):
            for i in range(per_thread):
                try:
                    state.add_serial_id(base + i)
                    state.add_subject("subject-%d" % (base + i), base + i)
                except Exception as e:  # pragma: no cover - failure path
                    errors.append(repr(e))

        def hammer_encode():
            while not stop_encoding.is_set():
                try:
                    state.to_span_tags()
                    state.has_data()
                except Exception as e:  # pragma: no cover - failure path
                    errors.append(repr(e))

        workers = [threading.Thread(target=add_ids, args=(t * per_thread,)) for t in range(n_threads)]
        encoder = threading.Thread(target=hammer_encode)
        encoder.start()
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        stop_encoding.set()
        encoder.join()

        assert errors == [], f"races raised: {errors[:3]}"
        # Every distinct serial id (0..199) landed -> no lost update.
        assert state._serial_ids == set(range(n_threads * per_thread))
        assert len(state._serial_ids) == 200
        # Final encode is a clean, consistent snapshot.
        tags = state.to_span_tags()
        assert _decode_delta_varint(tags["ffe_flags_enc"]) == set(range(n_threads * per_thread))

    def test_finish_snapshot_consistent_under_concurrent_capture(self):
        # Concurrency (hook-level): the root-span-finish path snapshots+encodes
        # the accumulator while a background thread keeps mutating that same
        # state. The lock must give the finish a consistent snapshot (never a
        # mid-mutation crash). finally_after is driven on the MAIN thread (where
        # the root context is active); the background thread mutates the state
        # object directly to simulate a concurrent continuation.
        import threading

        from ddtrace.trace import tracer

        hook = self._hook()
        try:
            errors: list = []
            stop = threading.Event()
            with tracer.trace("root") as root:
                # Seed state on the main (in-context) thread.
                hook.finally_after(
                    _make_hook_context(),
                    _make_details(True, "on", {"__dd_split_serial_id": 1, "__dd_do_log": False}),
                    {},
                )
                state = hook._span_states[root]

                def churn():
                    i = 2
                    while not stop.is_set():
                        try:
                            state.add_serial_id(i % 199)
                            i += 1
                        except Exception as e:  # pragma: no cover - failure path
                            errors.append(repr(e))

                t = threading.Thread(target=churn)
                t.start()
                # Repeatedly snapshot while churn() mutates -> must never raise.
                for _ in range(2000):
                    try:
                        state.to_span_tags()
                    except Exception as e:  # pragma: no cover - failure path
                        errors.append(repr(e))
                stop.set()
                t.join()
            # On finish the tag was written from a consistent snapshot.
            assert errors == [], f"races raised: {errors[:3]}"
            assert root.get_tag("ffe_flags_enc") is not None
            assert root not in hook._span_states  # cleaned up on finish
        finally:
            hook.destroy()


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


class TestRealProviderIntegration:
    """WR-05: drive the ACTUAL DataDogProvider + OpenFeature client end-to-end so
    the provider->hook wiring (FlagNotFound -> ffe_runtime_defaults; do_log ->
    flag_metadata threading) is exercised by the real path, not hand-built dicts.

    Every other hook test in this file constructs FlagEvaluationDetails by hand
    and calls hook.finally_after directly. None of them prove that the provider
    actually PRODUCES the details shape the hook contracts on -- a regression in
    _provider.py (e.g. dropping METADATA_DO_LOG, or changing the FlagNotFound
    early-return so variant is no longer None) would pass the entire rest of the
    suite while silently breaking the L2 contract. These tests close that gap by
    going through api.set_provider (which registers get_provider_hooks() into the
    client) + client.get_*_details, with BOTH gates on.
    """

    @pytest.fixture(autouse=True)
    def clear_config(self):
        from ddtrace.internal.openfeature._config import _set_ffe_config

        _set_ffe_config(None)
        yield
        _set_ffe_config(None)

    @pytest.fixture
    def both_gates_on(self):
        # Provider gate AND span-enrichment gate both on -> get_provider_hooks()
        # returns the SpanEnrichmentHook, which api.set_provider wires into the
        # client's finally chain. A low init timeout keeps initialize() from
        # blocking on Remote Config in the unit environment.
        from tests.utils import override_global_config

        with override_global_config(
            {
                "experimental_flagging_provider_enabled": True,
                "experimental_flagging_provider_span_enrichment_enabled": True,
            }
        ):
            yield

    def _set_provider_with_config(self, config):
        """Load an FFE config, set a real DataDogProvider, return a client.

        A config MUST be loaded for the provider to reach READY and for the
        FlagNotFound branch (vs PROVIDER_NOT_READY) to fire on a missing flag.
        """
        from openfeature import api

        from ddtrace.internal.openfeature._native import process_ffe_configuration
        from ddtrace.openfeature import DataDogProvider

        process_ffe_configuration(config)
        provider = DataDogProvider(initialization_timeout=0.1)
        api.set_provider(provider)
        return provider, api.get_client()

    def test_provider_hook_is_wired_into_client(self, both_gates_on):
        # Sanity: with both gates on, the span-enrichment hook is constructed and
        # exposed via get_provider_hooks (the seam api.set_provider consumes).
        from ddtrace.openfeature import DataDogProvider

        provider = DataDogProvider(initialization_timeout=0.1)
        try:
            assert provider._span_enrichment_hook is not None
            assert provider._span_enrichment_hook in provider.get_provider_hooks()
        finally:
            provider._span_enrichment_hook.destroy()

    def test_real_provider_flag_not_found_writes_runtime_defaults(self, both_gates_on):
        # WR-05 (a): a genuinely-not-found flag goes through the provider's real
        # FlagNotFound early-return (variant=None, no flag_metadata), the client
        # invokes the registered span-enrichment finally hook, and the root span
        # carries ffe_runtime_defaults == {flag_key: String(default_value)}.
        from openfeature import api

        from ddtrace.trace import tracer

        # Config is loaded (so the provider is READY) but does NOT contain the
        # queried flag -> the native eval returns FlagNotFound.
        config = create_config(create_boolean_flag("some-other-flag", enabled=True, default_value=True))
        _provider, client = self._set_provider_with_config(config)
        try:
            with tracer.trace("root") as root:
                value = client.get_string_value("definitely-missing-flag", "fallback-default")
                assert value == "fallback-default"  # default returned
            raw = root.get_tag("ffe_runtime_defaults")
            assert raw is not None, "FlagNotFound should produce ffe_runtime_defaults via the real hook path"
            decoded = json.loads(raw)
            assert decoded == {"definitely-missing-flag": "fallback-default"}
            # No serial id on a not-found flag -> no flags tag.
            assert root.get_tag("ffe_flags_enc") is None
        finally:
            api.shutdown()

    def test_real_provider_flag_not_found_null_default_renders_node_null(self, both_gates_on):
        # WR-01 x WR-05: a not-found OBJECT flag whose default is None must render
        # "null" (Node String(null)) -- the cross-SDK byte the L2 decoder expects --
        # driven entirely through the real provider + client, not a hand-built dict.
        from openfeature import api

        from ddtrace.trace import tracer

        config = create_config(create_boolean_flag("unrelated", enabled=True, default_value=True))
        _provider, client = self._set_provider_with_config(config)
        try:
            with tracer.trace("root") as root:
                # get_object_details with a None default + missing flag -> default None.
                details = client.get_object_details("missing-object-flag", None)
                assert details.value is None
            raw = root.get_tag("ffe_runtime_defaults")
            assert raw is not None
            decoded = json.loads(raw)
            assert decoded == {"missing-object-flag": "null"}
        finally:
            api.shutdown()

    def test_real_provider_success_threads_do_log_metadata(self, both_gates_on):
        # WR-05 (b): on a successful evaluation the provider threads __dd_do_log
        # into flag_metadata (the field the hook reads to gate subjects). Assert
        # the SHAPE the provider actually emits -- the exact contract the hook and
        # the L2 subjects scenario depend on -- via the real resolve path.
        from openfeature import api

        from ddtrace.internal.openfeature._span_enrichment import METADATA_DO_LOG

        config = create_config(create_string_flag("present-flag", "hello", enabled=True))
        provider, _client = self._set_provider_with_config(config)
        try:
            details = provider.resolve_string_details("present-flag", "default")
            assert details.value == "hello"
            assert details.variant is not None  # success path -> variant present
            # The provider lifts native do_log into the OpenFeature flag_metadata
            # dict that the enrichment hook consumes (test fixtures set doLog=true).
            assert METADATA_DO_LOG in details.flag_metadata
            assert details.flag_metadata[METADATA_DO_LOG] is True
        finally:
            api.shutdown()

    def test_real_provider_flag_not_found_details_shape(self, both_gates_on):
        # WR-05: the FlagNotFound FlagResolutionDetails the provider emits has
        # variant=None (the exact field the hook's runtime-default branch keys on).
        # Guards against a regression to the early-return shape.
        from openfeature import api
        from openfeature.flag_evaluation import Reason

        config = create_config(create_boolean_flag("unrelated2", enabled=True, default_value=True))
        provider, _client = self._set_provider_with_config(config)
        try:
            details = provider.resolve_string_details("nope-not-here", "d")
            assert details.variant is None
            assert details.reason == Reason.ERROR
        finally:
            api.shutdown()

    def test_real_provider_unicode_string_default_raw_utf8_bytes(self, both_gates_on):
        # WR-01 byte-parity: a not-found flag whose STRING default contains
        # non-ASCII must serialize as RAW UTF-8 in ffe_runtime_defaults (Node
        # JSON.stringify never \uXXXX-escapes). ensure_ascii=True (the json.dumps
        # default) would emit 🎉 -- a byte-for-byte divergence from
        # Node even though json.loads() decodes it to the same value. Assert on
        # the RAW tag bytes, not just the decoded value, to lock the wire format.
        from openfeature import api

        from ddtrace.trace import tracer

        config = create_config(create_boolean_flag("unrelated-uni", enabled=True, default_value=True))
        _provider, client = self._set_provider_with_config(config)
        try:
            with tracer.trace("root") as root:
                value = client.get_string_value("missing-unicode-flag", "🎉café")
                assert value == "🎉café"
            raw = root.get_tag("ffe_runtime_defaults")
            assert raw is not None
            # RAW bytes contain the literal UTF-8 characters, NOT \u escapes.
            assert "🎉café" in raw
            assert "\\u" not in raw
            # Still valid JSON decoding back to the original value (Node parity).
            assert json.loads(raw) == {"missing-unicode-flag": "🎉café"}
        finally:
            api.shutdown()

    def test_real_provider_unicode_object_default_raw_utf8_json(self, both_gates_on):
        # WR-01 byte-parity for OBJECT defaults: a not-found flag whose OBJECT
        # default carries Unicode must serialize via JSON.stringify-equivalent
        # compact + raw-UTF-8 json.dumps. The inner object string is itself a
        # JSON value stored inside the outer map; both layers must be raw UTF-8.
        from openfeature import api

        from ddtrace.trace import tracer

        config = create_config(create_boolean_flag("unrelated-obj", enabled=True, default_value=True))
        _provider, client = self._set_provider_with_config(config)
        try:
            with tracer.trace("root") as root:
                details = client.get_object_details("missing-object-uni", {"msg": "🎉", "name": "café"})
                assert details.value == {"msg": "🎉", "name": "café"}
            raw = root.get_tag("ffe_runtime_defaults")
            assert raw is not None
            # No \u escaping anywhere in the wire bytes.
            assert "\\u" not in raw
            assert "🎉" in raw and "café" in raw
            # Outer map decodes; the inner value is compact JSON.stringify bytes.
            decoded = json.loads(raw)
            assert decoded == {"missing-object-uni": '{"msg":"🎉","name":"café"}'}
            # And that inner string is itself valid JSON matching Node semantics.
            assert json.loads(decoded["missing-object-uni"]) == {"msg": "🎉", "name": "café"}
        finally:
            api.shutdown()

    def test_real_provider_object_default_compact_json_no_str_dict(self, both_gates_on):
        # WR-01: an OBJECT runtime default must be compact JSON.stringify
        # ({"a":1,"b":[2,3]}), NOT Python str(dict) ("{'a': 1, ...}") and NOT
        # space-padded json. Driven through the real provider + client.
        from openfeature import api

        from ddtrace.trace import tracer

        config = create_config(create_boolean_flag("unrelated-cmp", enabled=True, default_value=True))
        _provider, client = self._set_provider_with_config(config)
        try:
            with tracer.trace("root") as root:
                client.get_object_details("missing-compact", {"a": 1, "b": [2, 3]})
            raw = root.get_tag("ffe_runtime_defaults")
            assert raw is not None
            decoded = json.loads(raw)
            # Exact compact bytes, no spaces, no Python repr.
            assert decoded == {"missing-compact": '{"a":1,"b":[2,3]}'}
        finally:
            api.shutdown()
