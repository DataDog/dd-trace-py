"""Tests for the cross-SDK PII contract on EVP flagevaluation events."""

import json
import os
import typing
from unittest import mock
from unittest.mock import MagicMock
from uuid import UUID

from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import FlagEvaluationDetails
from openfeature.flag_evaluation import FlagType
from openfeature.flag_evaluation import Reason
from openfeature.hook import HookContext
import pytest

from ddtrace.internal.openfeature import _flagevaluation_writer as writer_module
from ddtrace.internal.openfeature import _provider as provider_module
from ddtrace.internal.openfeature._config import _FfeSnapshot
from ddtrace.internal.openfeature._config import _get_ffe_config
from ddtrace.internal.openfeature._config import _get_ffe_snapshot
from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._flag_eval_evp_hook import FlagEvalEVPHook
from ddtrace.internal.openfeature._flageval_pii import TARGETING_KEY_HASH_PREFIX
from ddtrace.internal.openfeature._flageval_pii import prefixed_targeting_key_digest
from ddtrace.internal.openfeature._flagevaluation_writer import METADATA_OBSERVE_FULL_EVALUATION_DATA
from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter
from ddtrace.internal.openfeature._flagevaluation_writer import _Entry
from ddtrace.internal.openfeature._flagevaluation_writer import _EvalEvent
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.internal.openfeature._provider import DataDogProvider
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config
from tests.openfeature.config_helpers import create_string_flag
from tests.utils import override_global_config


CANONICAL_TARGETING_KEY = "jane.doe@datadoghq.com"
CANONICAL_HASHED_TARGETING_KEY = "sha256_b4698f9b6d186781fa8dc59e533578fa2d8379a46b1cf6db85cda6aa9c99e51b"


class TestHashTargetingKey:
    def test_matches_cross_sdk_canonical_vector(self) -> None:
        assert prefixed_targeting_key_digest(CANONICAL_TARGETING_KEY) == CANONICAL_HASHED_TARGETING_KEY

    def test_uses_prefixed_lowercase_sha256(self) -> None:
        result = prefixed_targeting_key_digest(CANONICAL_TARGETING_KEY)

        assert result is not None
        assert result.startswith(TARGETING_KEY_HASH_PREFIX)
        assert len(result) == 71
        assert all(character in "0123456789abcdef" for character in result[len(TARGETING_KEY_HASH_PREFIX) :])

    def test_preserves_explicit_empty_string(self) -> None:
        assert prefixed_targeting_key_digest("") == ""

    @pytest.mark.parametrize("invalid", [None, [], b"user", "\ud800"])
    def test_omits_missing_non_string_and_malformed_values(self, invalid: typing.Any) -> None:
        assert prefixed_targeting_key_digest(invalid) is None

    def test_hashes_exact_utf8_bytes_without_normalization(self) -> None:
        nfc = "jos\u00e9@datadoghq.com"
        nfd = "jose\u0301@datadoghq.com"
        inputs = [
            CANONICAL_TARGETING_KEY,
            " " + CANONICAL_TARGETING_KEY,
            CANONICAL_TARGETING_KEY + " ",
            CANONICAL_TARGETING_KEY.upper(),
            nfc,
            nfd,
        ]

        results = [prefixed_targeting_key_digest(value) for value in inputs]

        assert len(set(results)) == len(inputs)

    def test_str_subclass_cannot_override_hashed_bytes(self) -> None:
        class HostileString(str):
            def encode(self, *args, **kwargs):
                raise RuntimeError("subclass encode must not run")

        assert prefixed_targeting_key_digest(HostileString(CANONICAL_TARGETING_KEY)) == CANONICAL_HASHED_TARGETING_KEY


class TestFfeSnapshot:
    @pytest.fixture(autouse=True)
    def clear_snapshot(self):
        _set_ffe_config(None)
        yield
        _set_ffe_config(None)

    def test_snapshot_round_trips_atomically(self) -> None:
        config = MagicMock(name="ffe.Configuration")

        _set_ffe_config(_FfeSnapshot(config=config, observe_full_evaluation_data=True))

        snapshot = _get_ffe_snapshot()
        assert snapshot is not None
        assert snapshot.config is config
        assert snapshot.observe_full_evaluation_data is True
        assert _get_ffe_config() is config


class TestObserveFullEvaluationDataParsing:
    @pytest.fixture(autouse=True)
    def clear_snapshot(self):
        _set_ffe_config(None)
        yield
        _set_ffe_config(None)

    @staticmethod
    def minimal_ufc(value: typing.Any = None, *, include: bool = False, nested: bool = False) -> dict:
        config = create_config()
        if include:
            if nested:
                config["environment"]["observeFullEvaluationData"] = value
            else:
                config["observeFullEvaluationData"] = value
        return config

    @pytest.mark.parametrize(
        ("value", "include", "expected"),
        [
            (None, False, False),
            (False, True, False),
            (True, True, True),
            (None, True, False),
            ("true", True, False),
            (1, True, False),
            ([], True, False),
            ({}, True, False),
        ],
    )
    def test_only_exact_root_boolean_true_opts_in(self, value: typing.Any, include: bool, expected: bool) -> None:
        assert process_ffe_configuration(self.minimal_ufc(value, include=include)) is True

        snapshot = _get_ffe_snapshot()
        assert snapshot is not None
        assert snapshot.observe_full_evaluation_data is expected

    def test_nested_value_is_ignored(self) -> None:
        assert process_ffe_configuration(self.minimal_ufc(True, include=True, nested=True)) is True

        snapshot = _get_ffe_snapshot()
        assert snapshot is not None
        assert snapshot.observe_full_evaluation_data is False


class TestProviderConsentMetadata:
    @pytest.fixture(autouse=True)
    def clear_snapshot(self):
        _set_ffe_config(None)
        yield
        _set_ffe_config(None)

    @staticmethod
    def provider() -> DataDogProvider:
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            return DataDogProvider()

    def test_metadata_key_matches_approved_private_contract(self) -> None:
        assert METADATA_OBSERVE_FULL_EVALUATION_DATA == "__dd_observe_full_evaluation_data"

    @pytest.mark.parametrize("observe", [False, True])
    def test_success_stamps_consent(self, observe: bool) -> None:
        config = create_config(create_boolean_flag("test-flag"))
        config["observeFullEvaluationData"] = observe
        assert process_ffe_configuration(config) is True

        details = self.provider().resolve_boolean_details("test-flag", False)

        assert details.flag_metadata[METADATA_OBSERVE_FULL_EVALUATION_DATA] is observe

    def test_no_configuration_stamps_protected_mode(self) -> None:
        details = self.provider().resolve_boolean_details("test-flag", False)

        assert details.flag_metadata[METADATA_OBSERVE_FULL_EVALUATION_DATA] is False

    def test_inactive_provider_stamps_evaluated_consent(self) -> None:
        _set_ffe_config(_FfeSnapshot(config=MagicMock(name="ffe.Configuration"), observe_full_evaluation_data=True))
        provider = self.provider()
        provider._active = False

        details = provider.resolve_boolean_details("test-flag", False)

        assert details.reason == Reason.DISABLED
        assert details.flag_metadata[METADATA_OBSERVE_FULL_EVALUATION_DATA] is True

    @pytest.mark.parametrize(
        ("config", "flag_key", "expected_reason", "expected_error"),
        [
            (
                create_config(create_boolean_flag("other-flag")),
                "missing-flag",
                Reason.ERROR,
                ErrorCode.FLAG_NOT_FOUND,
            ),
            (
                create_config(create_string_flag("string-flag", "value")),
                "string-flag",
                Reason.ERROR,
                ErrorCode.TYPE_MISMATCH,
            ),
            (
                create_config(create_boolean_flag("disabled-flag", enabled=False)),
                "disabled-flag",
                Reason.DISABLED,
                None,
            ),
        ],
        ids=["flag-not-found", "native-error", "disabled-flag"],
    )
    def test_native_terminal_paths_stamp_evaluated_consent(
        self,
        config: dict,
        flag_key: str,
        expected_reason: Reason,
        expected_error: typing.Optional[ErrorCode],
    ) -> None:
        config["observeFullEvaluationData"] = True
        assert process_ffe_configuration(config) is True

        details = self.provider().resolve_boolean_details(flag_key, False)

        assert details.reason == expected_reason
        assert details.error_code == expected_error
        assert details.flag_metadata[METADATA_OBSERVE_FULL_EVALUATION_DATA] is True

    def test_consent_stays_bound_to_evaluated_snapshot(self, monkeypatch) -> None:
        evaluated_config = MagicMock(name="evaluated_config")
        replacement_config = MagicMock(name="replacement_config")
        _set_ffe_config(_FfeSnapshot(config=evaluated_config, observe_full_evaluation_data=True))

        def replace_live_snapshot(configuration, *args, **kwargs):
            assert configuration is evaluated_config
            _set_ffe_config(_FfeSnapshot(config=replacement_config, observe_full_evaluation_data=False))
            return None

        monkeypatch.setattr(provider_module, "resolve_flag", replace_live_snapshot)

        details = self.provider().resolve_boolean_details("test-flag", False)

        assert details.flag_metadata[METADATA_OBSERVE_FULL_EVALUATION_DATA] is True

    def test_exception_after_configuration_swap_keeps_evaluated_consent(self, monkeypatch) -> None:
        evaluated_config = MagicMock(name="evaluated_config")
        replacement_config = MagicMock(name="replacement_config")
        _set_ffe_config(_FfeSnapshot(config=evaluated_config, observe_full_evaluation_data=True))

        def replace_live_snapshot_and_raise(configuration, *args, **kwargs):
            assert configuration is evaluated_config
            _set_ffe_config(_FfeSnapshot(config=replacement_config, observe_full_evaluation_data=False))
            raise RuntimeError("evaluation failed")

        monkeypatch.setattr(provider_module, "resolve_flag", replace_live_snapshot_and_raise)

        details = self.provider().resolve_boolean_details("test-flag", False)

        assert details.reason == Reason.ERROR
        assert details.error_code == ErrorCode.GENERAL
        assert details.flag_metadata[METADATA_OBSERVE_FULL_EVALUATION_DATA] is True


class TestHookPrivacyCapture:
    @staticmethod
    def capture(
        consent: typing.Any = False,
        *,
        include_consent: bool = True,
        evaluation_context: typing.Any = None,
        error_message: typing.Optional[str] = None,
        error_code: typing.Optional[ErrorCode] = None,
    ):
        writer = MagicMock()
        hook = FlagEvalEVPHook(writer)
        context = evaluation_context or EvaluationContext(
            targeting_key=CANONICAL_TARGETING_KEY,
            attributes={"email": CANONICAL_TARGETING_KEY, "plan": "enterprise"},
        )
        hook_context = HookContext(
            flag_key="pii-flag",
            flag_type=FlagType.BOOLEAN,
            default_value=False,
            evaluation_context=context,
        )
        metadata = {METADATA_OBSERVE_FULL_EVALUATION_DATA: consent} if include_consent else {}
        details = FlagEvaluationDetails(
            flag_key="pii-flag",
            value=False,
            variant=None if error_code else "on",
            reason=Reason.ERROR if error_code else Reason.TARGETING_MATCH,
            flag_metadata=metadata,
            error_message=error_message,
            error_code=error_code,
        )

        hook.finally_after(hook_context, details, {})

        writer.enqueue.assert_called_once()
        return writer.enqueue.call_args.args[0]

    @pytest.mark.parametrize("metadata_value", [False, None, "true", 1, [], {}])
    def test_only_exact_true_enables_full_capture(self, metadata_value: typing.Any) -> None:
        event = self.capture(metadata_value)

        assert event.observe_full_evaluation_data is False
        assert event.attrs == {}

    def test_missing_metadata_fails_closed(self) -> None:
        event = self.capture(include_consent=False)

        assert event.observe_full_evaluation_data is False
        assert event.attrs == {}

    def test_protected_mode_never_reads_context_attributes(self) -> None:
        class ContextWithExplodingAttributes:
            targeting_key = CANONICAL_TARGETING_KEY

            @property
            def attributes(self):
                raise AssertionError("protected mode must not touch context attributes")

        event = self.capture(False, evaluation_context=ContextWithExplodingAttributes())

        assert event.attrs == {}

    def test_full_mode_borrows_raw_context_for_synchronous_snapshot(self) -> None:
        attributes = {"email": CANONICAL_TARGETING_KEY, "plan": "enterprise"}
        context = EvaluationContext(targeting_key=CANONICAL_TARGETING_KEY, attributes=attributes)

        event = self.capture(True, evaluation_context=context)

        assert event.observe_full_evaluation_data is True
        assert event.targeting_key == CANONICAL_TARGETING_KEY
        assert event.attrs is attributes

    def test_protected_error_uses_stable_code(self) -> None:
        message = 'For input string: "secret@example.com"'

        event = self.capture(False, error_message=message, error_code=ErrorCode.TYPE_MISMATCH)

        assert event.error_message == ErrorCode.TYPE_MISMATCH.value
        assert "secret@example.com" not in event.error_message

    def test_full_error_uses_stable_code(self) -> None:
        message = 'For input string: "secret@example.com"'

        event = self.capture(True, error_message=message, error_code=ErrorCode.TYPE_MISMATCH)

        assert event.error_message == ErrorCode.TYPE_MISMATCH.value


class TestWriterPrivacyBoundary:
    def test_event_constructor_defaults_to_protected(self) -> None:
        event = _EvalEvent("flag", "on", "allocation", "key", {"secret": "value"}, False, "", 1)
        assert event.observe_full_evaluation_data is False

    @pytest.mark.parametrize("observe", [False, "true", 1])
    def test_direct_aggregation_discards_protected_context(self, observe) -> None:
        writer = FlagEvaluationWriter()
        writer._aggregate(self.event(attrs={"email": "context-only-canary"}, observe=observe))
        entry = next(iter(writer._full.values()))
        assert entry.context_attrs == {}
        with mock.patch.object(writer, "_send_payload") as send:
            writer.periodic()
        raw = send.call_args.args[0]
        assert b"context-only-canary" not in raw
        assert "context" not in json.loads(raw)["flagEvaluations"][0]

    @pytest.mark.parametrize("observe", [False, "true", 1])
    def test_serializer_independently_omits_protected_context(self, observe) -> None:
        writer = FlagEvaluationWriter()
        writer._full[("pii-flag", "on", "allocation")] = _Entry(
            1, False, CANONICAL_HASHED_TARGETING_KEY, {"secret": "context-only-canary"}, "", observe
        )
        with mock.patch.object(writer, "_send_payload") as send:
            writer.periodic()
        raw = send.call_args.args[0]
        assert b"context-only-canary" not in raw
        assert "context" not in json.loads(raw)["flagEvaluations"][0]

    def test_protected_queue_retains_no_attribute_alias(self) -> None:
        attrs = {"nested": {"email": "context-only-canary"}}
        writer = FlagEvaluationWriter()
        writer.enqueue(self.event(attrs=attrs))
        queued = writer._queue.get_nowait()
        attrs["nested"]["email"] = "mutated-canary"
        assert queued.attrs == {}
        assert queued.attrs is not attrs

    @pytest.mark.parametrize("observe", [False, True])
    @pytest.mark.parametrize("degraded", [False, True])
    def test_serializer_independently_sanitizes_error_text(self, observe, degraded) -> None:
        writer = FlagEvaluationWriter()
        entries = writer._degraded if degraded else writer._full
        entries[("pii-flag", "on", "allocation")] = _Entry(1, True, None, {}, "error-only-canary", observe)
        with mock.patch.object(writer, "_send_payload") as send:
            writer.periodic()
        raw = send.call_args.args[0]
        row = json.loads(raw)["flagEvaluations"][0]
        assert row["error"] == {"message": "GENERAL"}
        assert row["runtime_default_used"] is True
        assert b"error-only-canary" not in raw

    @pytest.mark.parametrize("initial,incoming", [(False, False), (False, True), (True, False), (True, True)])
    def test_entry_fold_and_out_of_order_timestamps(self, initial, incoming) -> None:
        entry = _Entry(20, False, "", {}, "", initial)
        entry.observe(30, incoming)
        entry.observe(10, incoming)
        assert entry.count == 3
        assert entry.first_evaluation == 10
        assert entry.last_evaluation == 30
        assert entry.observe_full_evaluation_data is (initial and incoming)

    @pytest.mark.parametrize("observe", [False, True])
    def test_full_tier_merge_passes_consent_to_fold(self, observe) -> None:
        writer = FlagEvaluationWriter()
        event = self.event(observe=observe)
        writer._aggregate(event)
        entry = next(iter(writer._full.values()))
        with mock.patch.object(_Entry, "observe", autospec=True) as merge:
            writer._aggregate(event)
        merge.assert_called_once_with(entry, event.eval_time_ms, observe)

    @pytest.mark.parametrize("observe", [False, True])
    def test_invalid_key_counter_counts_inputs_not_rows(self, observe) -> None:
        writer = FlagEvaluationWriter()
        invalids = [UUID(int=1), 123, b"user", "\ud800"]
        for value in invalids + [None, "", "valid"]:
            writer.enqueue(self.event(targeting_key=value, observe=observe))
        with mock.patch.object(writer, "_send_payload") as send:
            with mock.patch.object(writer_module, "_count_metric") as count:
                writer.periodic()
                count.assert_any_call(writer_module.FLAG_EVALUATION_TARGETING_KEY_OMITTED_METRIC, 4, "invalid")
                assert not any(
                    c.args[0] == writer_module.FLAG_EVALUATION_DROPPED_METRIC and c.args[1]
                    for c in count.call_args_list
                )
                assert (
                    sum(row["evaluation_count"] for row in json.loads(send.call_args.args[0])["flagEvaluations"]) == 7
                )
                count.reset_mock()
                writer.periodic()
                count.assert_any_call(writer_module.FLAG_EVALUATION_TARGETING_KEY_OMITTED_METRIC, 0, "invalid")

    def test_error_allowlist_requires_explicit_openfeature_vocabulary_review(self) -> None:
        assert writer_module._PROTECTED_ERROR_CODES == {code.value for code in ErrorCode}

    def test_failed_omission_metric_does_not_lose_valid_or_invalid_key_evaluations(self) -> None:
        writer = FlagEvaluationWriter()
        writer.enqueue(self.event(targeting_key=123))
        writer.enqueue(self.event(targeting_key=CANONICAL_TARGETING_KEY))
        with mock.patch.object(
            writer_module.telemetry_writer, "add_count_metric", side_effect=RuntimeError("sink down")
        ) as metric:
            with mock.patch.object(writer, "_send_payload") as send:
                writer.periodic()
        metric.assert_called_once()
        assert metric.call_args.args[1] == writer_module.FLAG_EVALUATION_TARGETING_KEY_OMITTED_METRIC
        rows = json.loads(send.call_args.args[0])["flagEvaluations"]
        assert len(rows) == 2
        assert sum(row["evaluation_count"] for row in rows) == 2
        assert {row.get("targeting_key") for row in rows} == {None, CANONICAL_HASHED_TARGETING_KEY}

    @pytest.mark.parametrize("code", list(ErrorCode))
    @pytest.mark.parametrize("observe", [False, True])
    @pytest.mark.parametrize("degraded", [False, True])
    def test_direct_error_code_wire_contract(self, code, observe, degraded) -> None:
        writer = FlagEvaluationWriter()
        if degraded:
            writer._per_flag_count["pii-flag"] = writer_module.PER_FLAG_CAP
        writer._aggregate(self.event(observe=observe, error_message="error-only-canary", error_code=code.value))
        with mock.patch.object(writer, "_send_payload") as send:
            writer.periodic()
        raw = send.call_args.args[0]
        row = json.loads(raw)["flagEvaluations"][0]
        assert row["error"] == {"message": code.value}
        assert row["evaluation_count"] == 1
        assert b"error-only-canary" not in raw

    @staticmethod
    def event(
        *,
        targeting_key: typing.Any = CANONICAL_TARGETING_KEY,
        attrs: typing.Optional[typing.Mapping[str, typing.Any]] = None,
        observe: typing.Any = False,
        error_message: str = "",
        error_code: str = "",
    ) -> _EvalEvent:
        return _EvalEvent(
            flag_key="pii-flag",
            variant="on",
            allocation_key="allocation",
            targeting_key=targeting_key,
            attrs=attrs if attrs is not None else {},
            runtime_default=False,
            error_message=error_message,
            eval_time_ms=1_789_514_662_002,
            observe_full_evaluation_data=observe,
            error_code=error_code,
        )

    @staticmethod
    def flush_raw(events: typing.Iterable[_EvalEvent]) -> bytes:
        writer = FlagEvaluationWriter(interval=10.0)
        for event in events:
            writer.enqueue(event)
        with mock.patch.object(writer, "_send_payload") as send:
            writer.periodic()
        assert send.called
        payload = send.call_args.args[0]
        assert isinstance(payload, bytes)
        return payload

    @staticmethod
    def flush(events: typing.Iterable[_EvalEvent]) -> list[dict]:
        return json.loads(TestWriterPrivacyBoundary.flush_raw(events))["flagEvaluations"]

    def test_protected_payload_hashes_key_and_omits_context(self) -> None:
        rows = self.flush(
            [
                self.event(
                    attrs={"email": CANONICAL_TARGETING_KEY, "nested": {"secret": "value"}},
                    observe=False,
                )
            ]
        )

        assert rows[0]["targeting_key"] == CANONICAL_HASHED_TARGETING_KEY
        assert "context" not in rows[0]
        assert CANONICAL_TARGETING_KEY not in json.dumps(rows)

    def test_protected_raw_payload_contains_no_pii_canary(self) -> None:
        raw_payload = self.flush_raw(
            [
                self.event(
                    attrs={"email": CANONICAL_TARGETING_KEY, "nested": {"secret": "secret@example.com"}},
                    observe=False,
                    error_message='For input string: "secret@example.com"',
                    error_code=ErrorCode.TYPE_MISMATCH.value,
                )
            ]
        )

        assert CANONICAL_HASHED_TARGETING_KEY.encode() in raw_payload
        assert CANONICAL_TARGETING_KEY.encode() not in raw_payload
        assert b"secret@example.com" not in raw_payload
        assert b'"evaluation"' not in raw_payload

    @pytest.mark.parametrize("invalid_consent", ["false", 1, object()])
    def test_non_boolean_consent_fails_closed(self, invalid_consent: typing.Any) -> None:
        rows = self.flush(
            [
                self.event(
                    attrs={"email": CANONICAL_TARGETING_KEY},
                    observe=invalid_consent,
                    error_message='For input string: "secret@example.com"',
                    error_code=ErrorCode.TYPE_MISMATCH.value,
                )
            ]
        )

        assert rows[0]["targeting_key"] == CANONICAL_HASHED_TARGETING_KEY
        assert rows[0]["error"]["message"] == ErrorCode.TYPE_MISMATCH.value
        assert "context" not in rows[0]
        assert CANONICAL_TARGETING_KEY not in json.dumps(rows)
        assert "secret@example.com" not in json.dumps(rows)

    def test_full_payload_preserves_raw_key_and_bounded_context(self) -> None:
        rows = self.flush([self.event(attrs={"plan": "enterprise"}, observe=True)])

        assert rows[0]["targeting_key"] == CANONICAL_TARGETING_KEY
        assert rows[0]["context"]["evaluation"] == {"plan": "enterprise"}

    @pytest.mark.parametrize("observe", [False, True], ids=["protected", "full"])
    def test_explicit_empty_targeting_key_remains_present(self, observe: bool) -> None:
        rows = self.flush([self.event(targeting_key="", observe=observe)])

        assert rows[0]["targeting_key"] == ""

    @pytest.mark.parametrize("invalid", [None, [], b"user", "\ud800"])
    @pytest.mark.parametrize("observe", [False, True], ids=["protected", "full"])
    def test_invalid_targeting_key_is_omitted_without_dropping_event(self, invalid: typing.Any, observe: bool) -> None:
        rows = self.flush([self.event(targeting_key=invalid, observe=observe)])

        assert len(rows) == 1
        assert "targeting_key" not in rows[0]

    def test_consent_is_part_of_full_bucket_identity(self) -> None:
        rows = self.flush(
            [
                self.event(targeting_key="", observe=False),
                self.event(targeting_key="", observe=True),
            ]
        )

        assert len(rows) == 2
        assert all(row["evaluation_count"] == 1 for row in rows)

    @pytest.mark.parametrize("observations", [(True, False), (False, True)])
    def test_degraded_bucket_merges_consent_and_fails_closed(self, observations: tuple[bool, bool]) -> None:
        writer = FlagEvaluationWriter(interval=10.0)
        writer._per_flag_count["pii-flag"] = writer_module.PER_FLAG_CAP

        for observe in observations:
            writer._aggregate(
                self.event(
                    observe=observe,
                    error_message=ErrorCode.GENERAL.value,
                    error_code=ErrorCode.GENERAL.value,
                )
            )

        assert len(writer._degraded) == 1
        entry = next(iter(writer._degraded.values()))
        assert entry.count == 2
        assert entry.observe_full_evaluation_data is False
        with mock.patch.object(writer, "_send_payload") as send:
            writer.periodic()
        rows = json.loads(send.call_args.args[0])["flagEvaluations"]
        assert len(rows) == 1
        assert rows[0]["evaluation_count"] == 2
        assert "targeting_key" not in rows[0]
        assert "context" not in rows[0]

    def test_protected_buckets_do_not_key_on_context(self) -> None:
        rows = self.flush(
            [
                self.event(attrs={"segment": "one"}, observe=False),
                self.event(attrs={"segment": "two"}, observe=False),
            ]
        )

        assert len(rows) == 1
        assert rows[0]["evaluation_count"] == 2
        assert "context" not in rows[0]

    def test_full_buckets_still_key_on_context(self) -> None:
        rows = self.flush(
            [
                self.event(attrs={"segment": "one"}, observe=True),
                self.event(attrs={"segment": "two"}, observe=True),
            ]
        )

        assert len(rows) == 2

    def test_writer_reenforces_protected_error_redaction(self) -> None:
        message = 'For input string: "secret@example.com"'
        rows = self.flush(
            [
                self.event(
                    observe=False,
                    error_message=message,
                    error_code=ErrorCode.TYPE_MISMATCH.value,
                )
            ]
        )

        assert rows[0]["error"]["message"] == ErrorCode.TYPE_MISMATCH.value
        assert "secret@example.com" not in json.dumps(rows)

    @pytest.mark.parametrize("observe", [False, True])
    def test_writer_rejects_unknown_error_code(self, observe) -> None:
        rows = self.flush(
            [
                self.event(
                    observe=observe,
                    error_message="ignored raw message",
                    error_code="secret@example.com",
                )
            ]
        )

        assert rows[0]["error"]["message"] == ErrorCode.GENERAL.value
        assert "secret@example.com" not in json.dumps(rows)

    def test_protected_enqueue_skips_context_snapshot_and_hashing(self) -> None:
        writer = FlagEvaluationWriter(interval=10.0)
        event = self.event(attrs={"secret": "value"}, observe=False)

        with mock.patch.object(writer_module, "flatten_and_prune_context") as snapshot:
            with mock.patch.object(writer_module, "prefixed_targeting_key_digest") as hash_key:
                writer.enqueue(event)

        snapshot.assert_not_called()
        hash_key.assert_not_called()

        with mock.patch.object(
            writer_module, "prefixed_targeting_key_digest", wraps=prefixed_targeting_key_digest
        ) as hash_key:
            writer._aggregate(writer._queue.get_nowait())

        hash_key.assert_called_once_with(CANONICAL_TARGETING_KEY)


class TestPrivacyLifecycleAndGates:
    @pytest.fixture(autouse=True)
    def clear_snapshot(self):
        _set_ffe_config(None)
        yield
        _set_ffe_config(None)

    @staticmethod
    def config(consent: bool, do_log: bool = True) -> dict:
        flag = create_boolean_flag("pii-flag")
        flag["allocations"][0]["doLog"] = do_log
        config = create_config(flag)
        config["observeFullEvaluationData"] = consent
        return config

    @staticmethod
    def render(consent: bool, do_log: bool, replacement_consent: typing.Optional[bool] = None) -> dict:
        assert process_ffe_configuration(TestPrivacyLifecycleAndGates.config(consent, do_log)) is True
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider()
        writer = FlagEvaluationWriter(interval=10.0)
        hook = FlagEvalEVPHook(writer)
        context = EvaluationContext(
            targeting_key=CANONICAL_TARGETING_KEY,
            attributes={"email": CANONICAL_TARGETING_KEY, "plan": "enterprise"},
        )
        details = provider.resolve_boolean_details("pii-flag", False, context)
        hook_context = HookContext(
            flag_key="pii-flag",
            flag_type=FlagType.BOOLEAN,
            default_value=False,
            evaluation_context=context,
        )
        hook.finally_after(hook_context, details, {})

        if replacement_consent is not None:
            assert process_ffe_configuration(TestPrivacyLifecycleAndGates.config(replacement_consent, do_log)) is True

        with mock.patch.object(writer, "_send_payload") as send:
            writer.periodic()
        row = json.loads(send.call_args.args[0])["flagEvaluations"][0]
        for field in ("timestamp", "first_evaluation", "last_evaluation"):
            row.pop(field, None)
        return row

    @pytest.mark.parametrize(
        ("consent_at_evaluation", "replacement_consent", "expected_key", "expect_context"),
        [
            (False, True, CANONICAL_HASHED_TARGETING_KEY, False),
            (True, False, CANONICAL_TARGETING_KEY, True),
        ],
        ids=["protected-stays-protected", "full-stays-full"],
    )
    def test_later_configuration_cannot_change_captured_consent(
        self,
        consent_at_evaluation: bool,
        replacement_consent: bool,
        expected_key: str,
        expect_context: bool,
    ) -> None:
        row = self.render(consent_at_evaluation, True, replacement_consent)

        assert row["targeting_key"] == expected_key
        assert ("context" in row) is expect_context

    @pytest.mark.parametrize("consent", [False, True], ids=["protected", "full"])
    def test_do_log_does_not_affect_evp_shape(self, consent: bool) -> None:
        without_exposure = self.render(consent, False)
        with_exposure = self.render(consent, True)

        assert without_exposure == with_exposure

    @pytest.mark.parametrize("consent", [False, True], ids=["protected", "full"])
    def test_kill_switch_disables_evp_regardless_of_consent(self, consent: bool) -> None:
        assert process_ffe_configuration(self.config(consent)) is True

        with mock.patch.dict(os.environ, {"DD_FLAGGING_EVALUATION_COUNTS_ENABLED": "false"}):
            with override_global_config({"experimental_flagging_provider_enabled": True}):
                provider = DataDogProvider()

        assert provider._flag_eval_evp_writer is None
        assert provider._flag_eval_evp_hook is None
        assert all(not isinstance(hook, FlagEvalEVPHook) for hook in provider.get_provider_hooks())
