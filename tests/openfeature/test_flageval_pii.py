"""Tests for the cross-SDK PII contract on EVP flagevaluation events."""

import json
import os
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

from ddtrace.internal.openfeature import _flagevaluation_writer as writer_module
from ddtrace.internal.openfeature import _provider as provider_module
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
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config
from tests.utils import override_global_config


CANONICAL_TARGETING_KEY = "jane.doe@datadoghq.com"
CANONICAL_HASHED_TARGETING_KEY = "sha256_b4698f9b6d186781fa8dc59e533578fa2d8379a46b1cf6db85cda6aa9c99e51b"


class TestHashTargetingKey:
    def test_matches_cross_sdk_canonical_vector(self) -> None:
        assert hash_targeting_key(CANONICAL_TARGETING_KEY) == CANONICAL_HASHED_TARGETING_KEY

    def test_uses_prefixed_lowercase_sha256(self) -> None:
        result = hash_targeting_key(CANONICAL_TARGETING_KEY)

        assert result is not None
        assert result.startswith(TARGETING_KEY_HASH_PREFIX)
        assert len(result) == 71
        assert all(character in "0123456789abcdef" for character in result[len(TARGETING_KEY_HASH_PREFIX) :])

    def test_preserves_explicit_empty_string(self) -> None:
        assert hash_targeting_key("") == ""

    @pytest.mark.parametrize("invalid", [None, [], b"user", "\ud800"])
    def test_omits_missing_non_string_and_malformed_values(self, invalid: typing.Any) -> None:
        assert hash_targeting_key(invalid) is None

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

        results = [hash_targeting_key(value) for value in inputs]

        assert len(set(results)) == len(inputs)

    def test_does_not_mutate_the_input(self) -> None:
        value = "  Jane.Doe@datadoghq.com  "

        hash_targeting_key(value)

        assert value == "  Jane.Doe@datadoghq.com  "

    def test_str_subclass_cannot_override_hashed_bytes(self) -> None:
        class HostileString(str):
            def encode(self, *args, **kwargs):
                raise RuntimeError("subclass encode must not run")

        assert hash_targeting_key(HostileString(CANONICAL_TARGETING_KEY)) == CANONICAL_HASHED_TARGETING_KEY


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

    def test_legacy_bare_config_fails_closed(self) -> None:
        config = MagicMock(name="ffe.Configuration")

        _set_ffe_config(config)

        snapshot = _get_ffe_snapshot()
        assert snapshot is not None
        assert snapshot.config is config
        assert snapshot.observe_full_evaluation_data is False


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

    def test_metadata_key_matches_cross_sdk_contract(self) -> None:
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

    def test_full_error_preserves_message(self) -> None:
        message = 'For input string: "secret@example.com"'

        event = self.capture(True, error_message=message, error_code=ErrorCode.TYPE_MISMATCH)

        assert event.error_message == message


class TestWriterPrivacyBoundary:
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
    def flush(events: typing.Iterable[_EvalEvent]) -> list[dict]:
        writer = FlagEvaluationWriter(interval=10.0)
        for event in events:
            writer.enqueue(event)
        with mock.patch.object(writer, "_send_payload") as send:
            writer.periodic()
        assert send.called
        return json.loads(send.call_args.args[0])["flagEvaluations"]

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
    def test_degraded_bucket_preserves_consent_identity(self, observations: tuple[bool, bool]) -> None:
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

        assert len(writer._degraded) == 2
        assert {entry.observe_full_evaluation_data for entry in writer._degraded.values()} == {False, True}

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

    def test_writer_rejects_unknown_protected_error_code(self) -> None:
        rows = self.flush(
            [
                self.event(
                    observe=False,
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
            with mock.patch.object(writer_module, "hash_targeting_key") as hash_key:
                writer.enqueue(event)

        snapshot.assert_not_called()
        hash_key.assert_not_called()

        with mock.patch.object(writer_module, "hash_targeting_key", wraps=hash_targeting_key) as hash_key:
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
