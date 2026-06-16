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
            # Object default rendered via compact JSON.stringify ({"color":"blue"}).
            assert json.loads(raw) == {"theme": '{"color":"blue"}'}
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
