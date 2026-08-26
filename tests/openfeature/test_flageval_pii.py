"""
Tests for the cross-SDK PII contract in the flagevaluation EVP track.

Every SDK produces the same digest for the same subject, so hashed values join
across languages. This file pins that contract for dd-trace-py.
"""

import json
import time
import typing
from unittest import mock
from unittest.mock import MagicMock

from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import FlagEvaluationDetails
from openfeature.flag_evaluation import FlagType
from openfeature.flag_evaluation import Reason
from openfeature.hook import HookContext
import pytest

from ddtrace.internal.openfeature import _flagevaluation_writer as _fw_module
from ddtrace.internal.openfeature._config import _FfeSnapshot
from ddtrace.internal.openfeature._config import _get_ffe_config
from ddtrace.internal.openfeature._config import _get_ffe_snapshot
from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._flag_eval_evp_hook import FlagEvalEVPHook
from ddtrace.internal.openfeature._flageval_pii import TARGETING_KEY_HASH_PREFIX
from ddtrace.internal.openfeature._flageval_pii import hash_targeting_key
from ddtrace.internal.openfeature._flagevaluation_writer import METADATA_OBSERVE_FULL_EVALUATION_DATA
from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter
from ddtrace.internal.openfeature._flagevaluation_writer import _EvalEvent
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.internal.openfeature._provider import DataDogProvider


# Canonical cross-SDK vector. Every SDK must reproduce this digest byte-for-byte
# for the same subject. Asserted here and in system-tests
# (tests/ffe/test_flag_eval_evp.py, once the manifest is flipped).
PII_CANONICAL_TARGETING_KEY = "jane.doe@datadoghq.com"
PII_CANONICAL_HASHED = "sha256_b4698f9b6d186781fa8dc59e533578fa2d8379a46b1cf6db85cda6aa9c99e51b"
CONSENT_METADATA_KEY = "__dd_observe_full_evaluation_data"


class TestHashTargetingKey:
    def test_canonical_vector(self):
        """The single load-bearing cross-SDK assertion."""
        assert hash_targeting_key(PII_CANONICAL_TARGETING_KEY) == PII_CANONICAL_HASHED

    def test_prefix_length_and_charset(self):
        """71 chars total, sha256_ prefix, 64 lowercase-hex digest."""
        got = hash_targeting_key(PII_CANONICAL_TARGETING_KEY)
        assert got is not None
        assert len(got) == 71
        assert got.startswith(TARGETING_KEY_HASH_PREFIX)
        hex_suffix = got[len(TARGETING_KEY_HASH_PREFIX) :]
        assert len(hex_suffix) == 64
        assert all(c in "0123456789abcdef" for c in hex_suffix)

    def test_empty_input_stays_empty(self) -> None:
        """An explicit empty targeting key remains present and empty."""
        assert hash_targeting_key("") == ""

    @pytest.mark.parametrize("invalid", [None, "\ud800", []])
    def test_invalid_input_is_omitted(self, invalid: typing.Any) -> None:
        """Missing and malformed values are omitted without aborting a flush."""
        assert hash_targeting_key(invalid) is None

    def test_does_not_normalize(self):
        """Every variant must produce a DIFFERENT digest from the canonical one.

        Trimming, case folding, or Unicode normalization would silently break the
        cross-SDK join. NFC vs NFD is the subtle case: same grapheme, different bytes.
        """
        # NFC precomposed U+00E9 vs NFD "e" + U+0301 combining acute. Use explicit
        # escapes so a text-editor autonormalize can't collapse the two.
        nfc_accent = "jos\u00e9@datadoghq.com"
        nfd_accent = "jose\u0301@datadoghq.com"
        assert nfc_accent.encode("utf-8") != nfd_accent.encode("utf-8"), (
            "NFC and NFD forms must have distinct UTF-8 bytes for this test to be meaningful"
        )

        variants = {
            "leading whitespace": " " + PII_CANONICAL_TARGETING_KEY,
            "trailing whitespace": PII_CANONICAL_TARGETING_KEY + " ",
            "uppercased": PII_CANONICAL_TARGETING_KEY.upper(),
            "NFC-composed accent": nfc_accent,
            "NFD-decomposed accent": nfd_accent,
        }
        seen = {PII_CANONICAL_HASHED: "canonical"}
        for name, input_str in variants.items():
            got = hash_targeting_key(input_str)
            assert got not in seen, f"{name} produced the same digest as {seen[got]}"
            seen[got] = name


class TestFfeSnapshot:
    """Storage semantics of _FfeSnapshot in _config.py."""

    def test_default_is_none(self):
        _set_ffe_config(None)
        try:
            assert _get_ffe_snapshot() is None
        finally:
            _set_ffe_config(None)

    def test_set_snapshot_round_trips(self):
        fake_config = MagicMock(name="ffe.Configuration")
        _set_ffe_config(_FfeSnapshot(config=fake_config, observe_full_evaluation_data=True))
        snap = _get_ffe_snapshot()
        try:
            assert snap is not None
            assert snap.config is fake_config
            assert snap.observe_full_evaluation_data is True
        finally:
            _set_ffe_config(None)

    def test_legacy_bare_config_is_consent_off(self):
        """A bare Configuration (existing test callers) is stored as consent-off."""
        fake_config = MagicMock(name="ffe.Configuration")
        _set_ffe_config(fake_config)
        snap = _get_ffe_snapshot()
        try:
            assert snap is not None
            assert snap.config is fake_config
            assert snap.observe_full_evaluation_data is False
        finally:
            _set_ffe_config(None)

    def test_get_ffe_config_returns_bare_config(self):
        """The legacy accessor still returns just the config."""
        fake_config = MagicMock(name="ffe.Configuration")
        _set_ffe_config(_FfeSnapshot(config=fake_config, observe_full_evaluation_data=True))
        try:
            assert _get_ffe_config() is fake_config
        finally:
            _set_ffe_config(None)


class TestUFCObserveFullEvaluationDataParsing:
    """Read side of the contract: the field is read from the UFC ROOT (sibling
    of `environment`), and any non-True value fails closed to False.
    """

    def _minimal_ufc(self, extra_root: dict = None, environment_extra: dict = None) -> dict:
        env = {"name": "Staging"}
        if environment_extra:
            env.update(environment_extra)
        # Native parser requires id/createdAt at the UFC root alongside format,
        # environment, and flags. Keep the shape minimal but valid so the parse
        # succeeds and the snapshot lands, letting these tests focus on the
        # observeFullEvaluationData read path.
        ufc = {
            "id": "test-config-pii",
            "createdAt": "2026-08-06T00:00:00Z",
            "format": "SERVER",
            "environment": env,
            "flags": {},
        }
        if extra_root:
            ufc.update(extra_root)
        return ufc

    def _snapshot_for(self, ufc: dict):
        _set_ffe_config(None)
        process_ffe_configuration(ufc)
        return _get_ffe_snapshot()

    def test_absent_is_false(self):
        try:
            snap = self._snapshot_for(self._minimal_ufc())
            assert snap is not None
            assert snap.observe_full_evaluation_data is False
        finally:
            _set_ffe_config(None)

    def test_explicit_false(self):
        try:
            snap = self._snapshot_for(self._minimal_ufc({"observeFullEvaluationData": False}))
            assert snap.observe_full_evaluation_data is False
        finally:
            _set_ffe_config(None)

    def test_explicit_true_opts_in(self):
        try:
            snap = self._snapshot_for(self._minimal_ufc({"observeFullEvaluationData": True}))
            assert snap.observe_full_evaluation_data is True
        finally:
            _set_ffe_config(None)

    def test_explicit_null_fails_closed(self):
        try:
            snap = self._snapshot_for(self._minimal_ufc({"observeFullEvaluationData": None}))
            assert snap.observe_full_evaluation_data is False
        finally:
            _set_ffe_config(None)

    @pytest.mark.parametrize("bad", ["true", "false", 1, 0, [], {}])
    def test_wrong_type_fails_closed(self, bad):
        try:
            snap = self._snapshot_for(self._minimal_ufc({"observeFullEvaluationData": bad}))
            assert snap.observe_full_evaluation_data is False
        finally:
            _set_ffe_config(None)

    def test_nested_under_environment_is_not_read(self):
        """FFL-2964 placement-drift regression guard.

        A parser that reads the field from environment would report True here
        and hash forever in production. The approved RFC keeps it at the UFC
        root.
        """
        try:
            snap = self._snapshot_for(self._minimal_ufc(environment_extra={"observeFullEvaluationData": True}))
            assert snap.observe_full_evaluation_data is False
        finally:
            _set_ffe_config(None)


class TestProviderStampsConsent:
    """The provider stamps observe_full_evaluation_data on flag_metadata on every
    return path, from the exact snapshot the evaluation ran against.
    """

    def _config_with_consent(self, observe: bool):
        """Build a minimal UFC dict with the given consent value."""
        return {
            "id": "test-config",
            "createdAt": "2026-01-01T00:00:00Z",
            "format": "SERVER",
            "observeFullEvaluationData": observe,
            "environment": {"name": "Staging"},
            "flags": {
                "test-bool": {
                    "key": "test-bool",
                    "enabled": True,
                    "variationType": "BOOLEAN",
                    "defaultVariation": "on",
                    "variations": {"on": {"key": "on", "value": True}},
                    "allocations": [
                        {
                            "key": "default",
                            "rules": [],
                            "splits": [{"variationKey": "on", "shards": []}],
                            "doLog": True,
                        }
                    ],
                },
            },
        }

    @pytest.fixture(autouse=True)
    def _clear_state(self):
        _set_ffe_config(None)
        yield
        _set_ffe_config(None)

    def _provider(self, monkeypatch):
        # Enable the provider so _resolve_details doesn't early-return DISABLED.
        monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED", "true")
        # Killswitch on for these tests -- we only care about the metadata stamp.
        monkeypatch.setenv("DD_FLAGGING_EVALUATION_COUNTS_ENABLED", "true")
        return DataDogProvider()

    def test_metadata_key_matches_contract_literal(self) -> None:
        assert METADATA_OBSERVE_FULL_EVALUATION_DATA == CONSENT_METADATA_KEY

    def test_success_path_stamps_consent_true(self, monkeypatch):
        process_ffe_configuration(self._config_with_consent(True))
        provider = self._provider(monkeypatch)

        details = provider.resolve_boolean_details("test-bool", False)
        assert details.flag_metadata[CONSENT_METADATA_KEY] is True

    def test_success_path_stamps_consent_false(self, monkeypatch):
        process_ffe_configuration(self._config_with_consent(False))
        provider = self._provider(monkeypatch)

        details = provider.resolve_boolean_details("test-bool", False)
        assert details.flag_metadata[CONSENT_METADATA_KEY] is False

    def test_flag_not_found_still_stamps_consent(self, monkeypatch):
        process_ffe_configuration(self._config_with_consent(True))
        provider = self._provider(monkeypatch)

        details = provider.resolve_boolean_details("no-such-flag", False)
        assert details.flag_metadata[CONSENT_METADATA_KEY] is True

    def test_no_configuration_fails_closed(self, monkeypatch):
        """PROVIDER_NOT_READY: no environment behind the evaluation, so no consent
        to honor. Must stamp False rather than leave the key absent-and-ambiguous.
        """
        # No process_ffe_configuration call -- snapshot stays None.
        provider = self._provider(monkeypatch)

        details = provider.resolve_boolean_details("anything", False)
        assert details.flag_metadata[CONSENT_METADATA_KEY] is False


class TestAggregatorConsent:
    """Consent semantics of full-tier aggregation."""

    @pytest.fixture
    def writer(self):
        return FlagEvaluationWriter(interval=10.0)

    def _event(
        self,
        observe_full_evaluation_data: bool,
        attrs: typing.Optional[dict] = None,
        targeting_key: typing.Any = "user-1",
    ) -> _EvalEvent:
        return _EvalEvent(
            flag_key="f",
            variant="on",
            allocation_key="alloc-1",
            targeting_key=targeting_key,
            attrs=attrs or {},
            runtime_default=False,
            error_message="",
            eval_time_ms=int(time.time() * 1000),
            observe_full_evaluation_data=observe_full_evaluation_data,
        )

    def test_consent_off_merges_distinct_contexts_into_one_bucket(self, writer):
        """concern:consent-off-bucket-keying regression: without consent the
        context is discarded at serialization, so distinct contexts must
        collapse into one bucket -- otherwise a high-cardinality attribute burns
        per-flag cardinality on privacy-protected traffic.
        """
        for i in range(5):
            writer._aggregate(self._event(observe_full_evaluation_data=False, attrs={"request_id": i}))
        assert len(writer._full) == 1
        entry = list(writer._full.values())[0]
        assert entry.count == 5
        assert entry.context_attrs == {}

    def test_consent_on_keeps_distinct_contexts_distinct(self, writer):
        for i in range(5):
            writer._aggregate(self._event(observe_full_evaluation_data=True, attrs={"request_id": i}))
        assert len(writer._full) == 5

    def test_mixed_consent_does_not_merge(self, writer):
        writer._aggregate(self._event(observe_full_evaluation_data=False))
        writer._aggregate(self._event(observe_full_evaluation_data=True))
        assert len(writer._full) == 2

    def test_protected_bucket_identity_matches_serialized_targeting_key(self, writer: FlagEvaluationWriter) -> None:
        for targeting_key in (None, "\ud800", []):
            writer._aggregate(self._event(observe_full_evaluation_data=False, targeting_key=targeting_key))
        writer._aggregate(self._event(observe_full_evaluation_data=False, targeting_key=""))
        writer._aggregate(self._event(observe_full_evaluation_data=False, targeting_key=PII_CANONICAL_TARGETING_KEY))

        assert len(writer._full) == 3
        by_targeting_key = {entry.targeting_key: entry for entry in writer._full.values()}
        assert by_targeting_key[None].count == 3
        assert by_targeting_key[""].count == 1
        assert by_targeting_key[PII_CANONICAL_HASHED].count == 1


class TestFlushSerialization:
    """Raw-wire assertions on the flagevaluations payload bytes.

    Assertions on raw JSON bytes catch raw values routed into unexpected fields,
    which a decode-then-inspect check would miss.
    """

    @pytest.fixture
    def writer(self):
        return FlagEvaluationWriter(interval=10.0)

    def _pii_event(self, observe_full_evaluation_data: bool):
        # Hook is what should skip attrs on consent-off. Simulate that here:
        attrs = (
            {}
            if not observe_full_evaluation_data
            else {
                "org_id": 1234,
                "user_email": PII_CANONICAL_TARGETING_KEY,
                "plan": "enterprise",
                "region": "us-east-1",
            }
        )
        return _EvalEvent(
            flag_key="pii-flag",
            variant="on",
            allocation_key="default-allocation",
            targeting_key=PII_CANONICAL_TARGETING_KEY,
            attrs=attrs,
            runtime_default=False,
            error_message="",
            eval_time_ms=int(time.time() * 1000),
            observe_full_evaluation_data=observe_full_evaluation_data,
        )

    def _flush_capture(self, writer):
        """Run periodic() and return the raw payload bytes _send_payload received."""
        with mock.patch.object(writer, "_send_payload") as mock_send:
            writer.periodic()
        assert mock_send.call_count >= 1, "expected at least one payload flush"
        payload_bytes, _ = mock_send.call_args[0]
        return payload_bytes

    def test_consent_off_hashes_and_omits_context(self, writer):
        writer._aggregate(self._pii_event(observe_full_evaluation_data=False))
        payload_bytes = self._flush_capture(writer)

        # Raw-wire assertions first: catches a raw value routed into an unexpected field.
        raw = payload_bytes.decode("utf-8")
        assert PII_CANONICAL_TARGETING_KEY not in raw
        assert "enterprise" not in raw
        assert "us-east-1" not in raw
        assert "user_email" not in raw

        decoded = json.loads(payload_bytes)
        assert len(decoded["flagEvaluations"]) == 1
        event = decoded["flagEvaluations"][0]
        assert event["targeting_key"] == PII_CANONICAL_HASHED
        # "Omitted" means the key is absent -- not None, not {}.
        assert "context" not in event

    def test_consent_on_emits_raw(self, writer):
        writer._aggregate(self._pii_event(observe_full_evaluation_data=True))
        payload_bytes = self._flush_capture(writer)

        decoded = json.loads(payload_bytes)
        event = decoded["flagEvaluations"][0]
        assert event["targeting_key"] == PII_CANONICAL_TARGETING_KEY
        assert "context" in event
        assert event["context"]["evaluation"]["plan"] == "enterprise"

    @pytest.mark.parametrize("consent", [False, True], ids=["protected", "full_data"])
    def test_explicit_empty_targeting_key_is_preserved(self, writer: FlagEvaluationWriter, consent: bool) -> None:
        writer._aggregate(self._pii_event(observe_full_evaluation_data=consent)._replace(targeting_key=""))
        payload_bytes = self._flush_capture(writer)

        event = json.loads(payload_bytes)["flagEvaluations"][0]
        assert "targeting_key" in event
        assert event["targeting_key"] == ""

    @pytest.mark.parametrize("consent", [False, True], ids=["protected", "full_data"])
    @pytest.mark.parametrize("invalid", [None, "\ud800", []], ids=["missing", "malformed_utf8", "wrong_type"])
    def test_missing_or_malformed_targeting_key_is_omitted(
        self,
        writer: FlagEvaluationWriter,
        consent: bool,
        invalid: typing.Any,
    ) -> None:
        writer._aggregate(self._pii_event(observe_full_evaluation_data=consent)._replace(targeting_key=invalid))
        payload_bytes = self._flush_capture(writer)

        event = json.loads(payload_bytes)["flagEvaluations"][0]
        assert "targeting_key" not in event

    @pytest.mark.parametrize("consent", [False, True], ids=["consent_off", "consent_on"])
    def test_error_message_pii_respects_consent(self, writer: FlagEvaluationWriter, consent: bool) -> None:
        error_pii = "secret@example.com"
        error_message = f'For input string: "{error_pii}"'
        hook = FlagEvalEVPHook(writer=writer)
        hook_context = HookContext(
            flag_key="pii-error-flag",
            flag_type=FlagType.STRING,
            default_value="fallback",
            evaluation_context=EvaluationContext(targeting_key="user-1"),
        )
        details = FlagEvaluationDetails(
            flag_key="pii-error-flag",
            value="fallback",
            variant=None,
            reason=Reason.ERROR,
            flag_metadata={METADATA_OBSERVE_FULL_EVALUATION_DATA: consent},
            error_message=error_message,
            error_code=ErrorCode.TYPE_MISMATCH,
        )

        hook.finally_after(hook_context, details, {})
        payload_bytes = self._flush_capture(writer)
        raw = payload_bytes.decode("utf-8")
        event = json.loads(payload_bytes)["flagEvaluations"][0]

        if consent:
            assert error_pii in raw
            assert event["error"]["message"] == error_message
        else:
            assert error_pii not in raw
            assert event["error"]["message"] == ErrorCode.TYPE_MISMATCH.value

    def test_invalid_targeting_keys_do_not_abort_flush(self, writer):
        valid = self._pii_event(observe_full_evaluation_data=False)._replace(flag_key="valid")
        invalid_unicode = self._pii_event(observe_full_evaluation_data=False)._replace(
            flag_key="invalid-unicode", targeting_key="\ud800"
        )
        invalid_type = self._pii_event(observe_full_evaluation_data=False)._replace(
            flag_key="invalid-type", targeting_key=[]
        )
        for event in (valid, invalid_unicode, invalid_type):
            writer.enqueue(event)

        payload_bytes = self._flush_capture(writer)
        decoded = json.loads(payload_bytes)
        events = {event["flag"]["key"]: event for event in decoded["flagEvaluations"]}

        assert events["valid"]["targeting_key"] == PII_CANONICAL_HASHED
        assert "targeting_key" not in events["invalid-unicode"]
        assert "targeting_key" not in events["invalid-type"]

    def test_degraded_tier_never_emits_subject_or_context(self):
        """Regardless of consent -- degraded already omits both. Proves the
        negative-control assertion on the degraded path for consent-on too.
        """
        original_global_cap = _fw_module.GLOBAL_CAP
        try:
            # globalCap 0 routes every new full key straight to the degraded tier.
            _fw_module.GLOBAL_CAP = 0
            for consent in (False, True):
                w = _fw_module.FlagEvaluationWriter(interval=10.0)
                w._aggregate(self._pii_event(observe_full_evaluation_data=consent))
                payload_bytes = self._flush_capture(w)
                raw = payload_bytes.decode("utf-8")
                assert PII_CANONICAL_TARGETING_KEY not in raw
                decoded = json.loads(payload_bytes)
                event = decoded["flagEvaluations"][0]
                assert "targeting_key" not in event
                assert "context" not in event
        finally:
            _fw_module.GLOBAL_CAP = original_global_cap


def _pii_flag_config(observe: bool, do_log: bool = True) -> dict:
    """Minimal single-flag UFC dict for lifecycle/DoLog/kill-switch tests."""
    return {
        "id": "test-config-pii",
        "createdAt": "2026-08-06T00:00:00Z",
        "format": "SERVER",
        "observeFullEvaluationData": observe,
        "environment": {"name": "Staging"},
        "flags": {
            "pii-flag": {
                "key": "pii-flag",
                "enabled": True,
                "variationType": "STRING",
                "defaultVariation": "on",
                "variations": {"on": {"key": "on", "value": "on-value"}},
                "allocations": [
                    {
                        "key": "default-allocation",
                        "rules": [],
                        "splits": [{"variationKey": "on", "shards": []}],
                        "doLog": do_log,
                    }
                ],
            },
        },
    }


class TestConsentLifecycle:
    """The Java-pilot L3 bug: consent read from live config at flush time. A
    later RC update retroactively applied another environment's policy. Both
    directions leak. dd-trace-py's design snapshots consent at evaluation time
    and carries it on the event; this test guards that.
    """

    @pytest.fixture(autouse=True)
    def _clear_state(self):
        _set_ffe_config(None)
        yield
        _set_ffe_config(None)

    @pytest.mark.parametrize(
        "consent_at_evaluation,consent_after_update,want_hashed",
        [
            # Later opt-in must NOT retroactively unmask an already-hashed subject.
            (False, True, True),
            # Later opt-out must NOT retroactively hash an already-consented subject.
            (True, False, False),
        ],
        ids=["off_to_on_stays_hashed", "on_to_off_stays_raw"],
    )
    def test_consent_is_not_re_read_after_evaluation(
        self, monkeypatch, consent_at_evaluation, consent_after_update, want_hashed
    ):
        monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("DD_FLAGGING_EVALUATION_COUNTS_ENABLED", "true")

        # 1. Install the consent-at-evaluation config.
        process_ffe_configuration(_pii_flag_config(consent_at_evaluation))

        # 2. Build provider + writer + hook, evaluate.
        provider = DataDogProvider()
        writer = FlagEvaluationWriter(interval=10.0)
        hook = FlagEvalEVPHook(writer=writer)

        eval_ctx = EvaluationContext(
            targeting_key=PII_CANONICAL_TARGETING_KEY,
            attributes={"plan": "enterprise"},
        )
        details = provider.resolve_string_details("pii-flag", "fallback", eval_ctx)

        # 3. Run the hook exactly as the SDK would.
        hook_context = HookContext(
            flag_key="pii-flag",
            flag_type=FlagType.STRING,
            default_value="fallback",
            evaluation_context=eval_ctx,
        )
        hook_details = FlagEvaluationDetails(
            flag_key="pii-flag",
            value=details.value,
            variant=details.variant,
            reason=details.reason,
            flag_metadata=details.flag_metadata,
            error_message=details.error_message,
            error_code=details.error_code,
        )
        hook.finally_after(hook_context, hook_details, {})

        # 4. Remote Config replaces the configuration BEFORE aggregation/flush.
        #    Nothing downstream of the evaluator may notice.
        process_ffe_configuration(_pii_flag_config(consent_after_update))

        # 5. Flush and inspect the wire bytes.
        with mock.patch.object(writer, "_send_payload") as mock_send:
            writer.periodic()
        payload_bytes, _ = mock_send.call_args[0]
        decoded = json.loads(payload_bytes)
        event = decoded["flagEvaluations"][0]

        if want_hashed:
            assert event["targeting_key"] == PII_CANONICAL_HASHED
            assert "context" not in event
        else:
            assert event["targeting_key"] == PII_CANONICAL_TARGETING_KEY
            assert event["context"]["evaluation"]["plan"] == "enterprise"


class TestDoLogNonImpact:
    """The RFC's `DoLog` non-impact proof: for each consent value, the emitted
    shape must be identical across do_log values -- ignoring wall-clock fields.
    """

    @pytest.fixture(autouse=True)
    def _clear_state(self):
        _set_ffe_config(None)
        yield
        _set_ffe_config(None)

    @pytest.mark.parametrize("consent", [False, True], ids=["consent_off", "consent_on"])
    def test_do_log_does_not_affect_emitted_shape(self, monkeypatch, consent):
        monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("DD_FLAGGING_EVALUATION_COUNTS_ENABLED", "true")

        payload_shapes: dict = {}
        for do_log in (False, True):
            process_ffe_configuration(_pii_flag_config(consent, do_log=do_log))
            provider = DataDogProvider()
            writer = FlagEvaluationWriter(interval=10.0)
            hook = FlagEvalEVPHook(writer=writer)

            eval_ctx = EvaluationContext(
                targeting_key=PII_CANONICAL_TARGETING_KEY,
                attributes={"plan": "enterprise"},
            )
            details = provider.resolve_string_details("pii-flag", "fallback", eval_ctx)

            hook_context = HookContext(
                flag_key="pii-flag",
                flag_type=FlagType.STRING,
                default_value="fallback",
                evaluation_context=eval_ctx,
            )
            hook_details = FlagEvaluationDetails(
                flag_key="pii-flag",
                value=details.value,
                variant=details.variant,
                reason=details.reason,
                flag_metadata=details.flag_metadata,
                error_message=details.error_message,
                error_code=details.error_code,
            )
            hook.finally_after(hook_context, hook_details, {})

            with mock.patch.object(writer, "_send_payload") as mock_send:
                writer.periodic()
            payload_bytes, _ = mock_send.call_args[0]
            decoded = json.loads(payload_bytes)
            event = decoded["flagEvaluations"][0]
            # Drop wall-clock-derived fields so the diff isolates the PII shape.
            event.pop("timestamp", None)
            event.pop("first_evaluation", None)
            event.pop("last_evaluation", None)
            payload_shapes[do_log] = event

        assert payload_shapes[False] == payload_shapes[True], (
            f"DoLog must not affect the emitted shape:\n"
            f"  do_log=False: {payload_shapes[False]}\n"
            f"  do_log=True:  {payload_shapes[True]}"
        )

        # And the shape must be the correct one for the consent value.
        if consent:
            assert payload_shapes[True]["targeting_key"] == PII_CANONICAL_TARGETING_KEY
        else:
            assert payload_shapes[True]["targeting_key"] == PII_CANONICAL_HASHED


class TestKillSwitch:
    """DD_FLAGGING_EVALUATION_COUNTS_ENABLED=false disables the EVP flagevaluation
    track entirely and always wins over observeFullEvaluationData.
    """

    @pytest.fixture(autouse=True)
    def _clear_state(self):
        _set_ffe_config(None)
        yield
        _set_ffe_config(None)

    @pytest.mark.parametrize("consent", [False, True], ids=["consent_off", "consent_on"])
    def test_kill_switch_off_constructs_no_writer_and_no_hook(self, monkeypatch, consent):
        monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("DD_FLAGGING_EVALUATION_COUNTS_ENABLED", "false")

        process_ffe_configuration(_pii_flag_config(consent))

        provider = DataDogProvider()

        assert provider._flag_eval_evp_writer is None
        assert provider._flag_eval_evp_hook is None
        # The provider's hook list must omit the EVP hook too.
        hook_types = {type(h).__name__ for h in provider.get_provider_hooks()}
        assert "FlagEvalEVPHook" not in hook_types
