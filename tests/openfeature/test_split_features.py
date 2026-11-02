"""Split features in flag metadata, the shared exposure decision and ExposureHook."""

import copy
import json
import logging
import pickle
from unittest import mock

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import FlagEvaluationDetails
from openfeature.hook import Hook
import pytest

from ddtrace.internal.openfeature import _split_features
from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._flag_eval_evp_hook import FlagEvalEVPHook
from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.internal.openfeature._provider import ExposureLoggingHook
from ddtrace.internal.openfeature.writer import ExposureWriter
from ddtrace.openfeature import DataDogProvider
from ddtrace.openfeature import ExposureEvaluation
from ddtrace.openfeature import ExposureHook
from tests.openfeature.conftest import set_openfeature_provider
from tests.utils import override_global_config


WRITER = "ddtrace.internal.openfeature._provider.get_exposure_writer"

HOLDOUT_FEATURES = [
    {"key": "holdout.key", "value": "06ad", "destinations": ["HOOK"]},
    {"key": "holdout.assignment_group", "value": "status_quo", "destinations": ["HOOK"]},
    {"key": "holdout.should_include_in_holdout_analysis", "value": False, "destinations": ["HOOK"]},
]


def _config(features=None, variant="on", serial_id=1001, do_log=True, flag_key="flag"):
    split = {"variationKey": variant, "shards": [], "serialId": serial_id}
    if features is not None:
        split["features"] = features
    return {
        "createdAt": "2026-10-01T00:00:00Z",
        "environment": {"name": "test"},
        "flags": {
            flag_key: {
                "key": flag_key,
                "enabled": True,
                "variationType": "STRING",
                "variations": {v: {"key": v, "value": v} for v in ("on", "off")},
                "allocations": [{"key": "alloc", "splits": [split], "doLog": do_log}],
            }
        },
    }


@pytest.fixture(autouse=True)
def _reset():
    _set_ffe_config(None)
    _split_features._warned_destinations.clear()
    api.set_evaluation_context(EvaluationContext())
    yield
    api.clear_hooks()
    api.clear_providers()
    _set_ffe_config(None)


@pytest.fixture
def provider():
    with override_global_config({"experimental_flagging_provider_enabled": True}):
        provider = DataDogProvider()
        set_openfeature_provider(provider)
        yield provider


@pytest.fixture
def writer():
    with mock.patch(WRITER) as get_writer:
        yield get_writer.return_value


@pytest.fixture
def evaluations():
    received = []
    api.add_hooks([ExposureHook(received.append)])
    return received


def _evaluate(subject="user-1", flag_key="flag"):
    return api.get_client().get_string_details(flag_key, "default", EvaluationContext(targeting_key=subject))


def _prefixed(metadata, prefix):
    return {k: v for k, v in metadata.items() if k.startswith(prefix)}


class TestFeatureParsing:
    def test_mixed_value_types_reach_the_hook(self, provider, writer, evaluations):
        features = HOLDOUT_FEATURES + [{"key": "holdout.fraction", "value": 0.25, "destinations": ["HOOK"]}]
        process_ffe_configuration(_config(features))

        details = _evaluate()

        assert details.value == "on"
        assert dict(evaluations[0].features) == {
            "holdout.key": "06ad",
            "holdout.assignment_group": "status_quo",
            "holdout.should_include_in_holdout_analysis": False,
            "holdout.fraction": 0.25,
        }
        assert details.flag_metadata["__dd_feature.holdout.should_include_in_holdout_analysis"] is False

    @pytest.mark.parametrize(
        "features",
        [None, "not-a-list", {"key": "k"}, [1, "x", None], [{"key": "k", "value": [1], "destinations": ["HOOK"]}]],
    )
    def test_missing_or_malformed_features_never_fail_the_flag(self, provider, writer, features):
        config = _config()
        config["flags"]["flag"]["allocations"][0]["splits"][0]["features"] = features
        process_ffe_configuration(config)

        details = _evaluate()

        assert details.error_code is None
        assert details.value == "on"
        assert _prefixed(details.flag_metadata, "__dd_feature.") == {}

    def test_unsupported_value_type_is_skipped(self, provider, writer, evaluations):
        process_ffe_configuration(
            _config(
                [
                    {"key": "obj", "value": {"a": 1}, "destinations": ["HOOK"]},
                    {"key": "kept", "value": "v", "destinations": ["HOOK"]},
                ]
            )
        )

        _evaluate()

        assert dict(evaluations[0].features) == {"kept": "v"}


class TestFeatureRouting:
    def test_each_destination_uses_its_own_prefix(self, provider, writer, evaluations):
        process_ffe_configuration(
            _config(
                [
                    {"key": "hook", "value": "h", "destinations": ["HOOK"]},
                    {"key": "exposure", "value": "e", "destinations": ["EXPOSURE"]},
                    {"key": "evaluation", "value": "v", "destinations": ["EVALUATION"]},
                ]
            )
        )

        metadata = _evaluate().flag_metadata

        assert _prefixed(metadata, "__dd_feature.") == {"__dd_feature.hook": "h"}
        assert _prefixed(metadata, "__dd_exposure_feature.") == {"__dd_exposure_feature.exposure": "e"}
        assert _prefixed(metadata, "__dd_evaluation_feature.") == {"__dd_evaluation_feature.evaluation": "v"}
        assert metadata["__dd_has_evaluation_features"] is True
        assert dict(evaluations[0].features) == {"hook": "h"}

    def test_unknown_destination_is_ignored_and_warned_once(self, provider, writer):
        process_ffe_configuration(
            _config(
                [
                    {"key": "both", "value": "b", "destinations": ["FUTURE", "HOOK"]},
                    {"key": "nowhere", "value": "n", "destinations": ["FUTURE"]},
                ]
            )
        )

        # Patched because the ddtrace logger rate-limits repeats on its own.
        with mock.patch.object(_split_features, "log") as log:
            metadata = _evaluate().flag_metadata
            _evaluate()

        assert [k for k in metadata if "nowhere" in k] == []
        assert metadata["__dd_feature.both"] == "b"
        assert log.warning.call_args_list == [mock.call("Ignoring unknown feature destination %s", "FUTURE")]

    def test_warned_destinations_are_capped(self):
        with mock.patch.object(_split_features, "log") as log:
            metadata = {}
            features = [("k%d" % i, "v", ("D%d" % i,)) for i in range(_split_features.MAX_WARNED_DESTINATIONS + 5)]
            _split_features.add_split_features(metadata, features)

        assert log.warning.call_count == _split_features.MAX_WARNED_DESTINATIONS
        assert metadata == {}

    def test_repeated_key_keeps_the_first_valid_feature_for_every_destination(self, provider, writer):
        process_ffe_configuration(
            _config(
                [
                    {"key": "k", "value": {"bad": True}, "destinations": ["HOOK"]},
                    {"key": "k", "value": "first", "destinations": ["HOOK"]},
                    {"key": "k", "value": "second", "destinations": ["HOOK", "EXPOSURE"]},
                ]
            )
        )

        metadata = _evaluate().flag_metadata

        assert metadata["__dd_feature.k"] == "first"
        assert "__dd_exposure_feature.k" not in metadata

    def test_split_without_features_adds_no_metadata(self, provider, writer):
        process_ffe_configuration(_config())
        plain = set(_evaluate().flag_metadata)
        _set_ffe_config(None)
        process_ffe_configuration(_config([{"key": "k", "value": "v", "destinations": ["HOOK"]}]))
        with_features = set(_evaluate(subject="user-2").flag_metadata)

        assert with_features - plain == {"__dd_feature.k"}
        assert not [k for k in plain if k.startswith(("__dd_feature", "__dd_exposure_feature", "__dd_evaluation"))]


class TestExposureDecision:
    def test_first_exposure_then_repeat(self, provider, writer, evaluations):
        process_ffe_configuration(_config())

        first = _evaluate()
        _evaluate()

        assert first.flag_metadata["__dd_exposure_cache_hit"] is False
        assert [(e.is_exposure, e.is_cache_hit, e.should_send) for e in evaluations] == [
            (True, False, True),
            (True, True, False),
        ]
        assert writer.enqueue.call_count == 1

    def test_variant_change_is_a_new_exposure(self, provider, writer, evaluations):
        process_ffe_configuration(_config(variant="on"))
        _evaluate()
        process_ffe_configuration(_config(variant="off"))
        _evaluate()

        assert [e.should_send for e in evaluations] == [True, True]
        assert writer.enqueue.call_count == 2

    def test_serial_id_change_is_a_new_exposure(self, provider, writer, evaluations):
        process_ffe_configuration(_config(serial_id=1))
        _evaluate()
        process_ffe_configuration(_config(serial_id=2))
        _evaluate()

        assert [e.should_send for e in evaluations] == [True, True]
        assert writer.enqueue.call_count == 2

    def test_allocation_without_exposure_logging_is_not_an_exposure(self, provider, writer, evaluations):
        process_ffe_configuration(_config(do_log=False))

        metadata = _evaluate().flag_metadata

        assert "__dd_exposure_cache_hit" not in metadata
        assert [(e.is_exposure, e.should_send) for e in evaluations] == [(False, False)]
        writer.enqueue.assert_not_called()

    def test_failed_evaluation_sends_and_records_nothing(self, provider, writer, evaluations):
        class FailingAfterHook(Hook):
            def after(self, hook_context, details, hints):
                raise RuntimeError("rejected")

        process_ffe_configuration(_config())
        api.add_hooks([FailingAfterHook()])

        failed = _evaluate()
        api.clear_hooks()
        api.add_hooks([ExposureHook(evaluations.append)])
        _evaluate()

        assert failed.error_code == ErrorCode.GENERAL
        assert [e.should_send for e in evaluations] == [False, True]
        assert writer.enqueue.call_count == 1

    def test_after_hooks_see_the_provisional_verdict(self, provider, writer):
        seen = []

        class ReadingHook(Hook):
            def after(self, hook_context, details, hints):
                seen.append(details.flag_metadata.get("__dd_exposure_cache_hit"))

        process_ffe_configuration(_config())
        api.add_hooks([ReadingHook()])

        _evaluate()
        _evaluate()

        assert seen == [False, True]

    def test_decision_with_an_error_code_records_nothing(self, provider, writer):
        process_ffe_configuration(_config())
        details = provider.resolve_string_details("flag", "default", EvaluationContext(targeting_key="u"))
        hook = ExposureLoggingHook()

        details.error_code = ErrorCode.GENERAL
        hook.finally_after(mock.Mock(), details, {})
        assert (writer.enqueue.call_count, len(provider._exposure_cache)) == (0, 0)

        details.error_code = None
        hook.finally_after(mock.Mock(), details, {})
        assert (writer.enqueue.call_count, len(provider._exposure_cache)) == (1, 1)

    def test_nested_evaluation_admits_one_exposure(self, provider, writer, evaluations):
        class NestedEvaluationHook(Hook):
            def __init__(self):
                self.nested = False

            def after(self, hook_context, details, hints):
                if not self.nested:
                    self.nested = True
                    _evaluate()

        process_ffe_configuration(_config())
        api.add_hooks([NestedEvaluationHook()])

        outer = _evaluate()

        assert writer.enqueue.call_count == 1
        assert outer.flag_metadata["__dd_exposure_cache_hit"] is True
        assert sorted(e.should_send for e in evaluations) == [False, True]

    def test_details_can_be_deep_copied_and_pickled(self, provider, writer, evaluations):
        copies = []

        class CopyingHook(Hook):
            def after(self, hook_context, details, hints):
                copies.append(copy.deepcopy(details))
                copies.append(pickle.loads(pickle.dumps(details)))

        process_ffe_configuration(_config())
        api.add_hooks([CopyingHook()])

        details = _evaluate()

        assert (details.value, details.error_code) == ("on", None)
        assert [type(c.flag_metadata) for c in copies] == [dict, dict]
        assert copies[0].flag_metadata["__dd_exposure_cache_hit"] is False
        assert [e.should_send for e in evaluations] == [True]
        assert writer.enqueue.call_count == 1

    def test_exposure_features_reach_the_exposure_event_but_not_the_cache(self, provider, writer):
        process_ffe_configuration(_config([{"key": "k", "value": "v1", "destinations": ["EXPOSURE"]}]))
        _evaluate()
        process_ffe_configuration(_config([{"key": "k", "value": "v2", "destinations": ["EXPOSURE"]}]))
        _evaluate()

        writer.enqueue.assert_called_once()
        assert writer.enqueue.call_args[0][0]["features"] == {"k": "v1"}

    def test_exposure_event_has_no_features_field_without_exposure_features(self, provider, writer):
        process_ffe_configuration(_config(HOLDOUT_FEATURES))
        _evaluate()

        assert "features" not in writer.enqueue.call_args[0][0]

    def test_hook_completes_the_decision_when_provider_hooks_do_not_run(self, provider, writer, evaluations):
        process_ffe_configuration(_config())

        with mock.patch.object(DataDogProvider, "get_provider_hooks", return_value=[]):
            _evaluate()
            _evaluate()

        assert [e.should_send for e in evaluations] == [True, False]
        writer.enqueue.assert_not_called()


class TestDatadogLoggingSwitch:
    @pytest.mark.parametrize("enabled", [False, True])
    def test_switch_stops_datadog_exposures_only(self, provider, evaluations, monkeypatch, enabled):
        from ddtrace.internal.settings.openfeature import config as ffe_config

        monkeypatch.setattr(ffe_config, "exposures_datadog_logging_enabled", enabled)
        exposure_writer = ExposureWriter()
        process_ffe_configuration(_config())

        with mock.patch(WRITER, return_value=exposure_writer):
            _evaluate()
            _evaluate()

        assert len(exposure_writer._buffer) == (1 if enabled else 0)
        assert [(e.should_send, e.is_cache_hit) for e in evaluations] == [(True, False), (False, True)]


class TestExposureHook:
    def test_callback_exception_does_not_affect_evaluation(self, provider, writer, caplog):
        calls = []

        def callback(evaluation):
            calls.append(evaluation)
            raise ValueError("boom")

        api.add_hooks([ExposureHook(callback)])
        process_ffe_configuration(_config())

        with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.openfeature._exposure_hook"):
            first = _evaluate()
            second = _evaluate(subject="user-2")

        assert (first.value, first.error_code) == ("on", None)
        assert (second.value, second.error_code) == ("on", None)
        assert len(calls) == 2
        levels = [r.levelno for r in caplog.records if "Exposure callback failed" in r.getMessage()]
        assert levels == [logging.WARNING, logging.DEBUG]
        assert writer.enqueue.call_count == 2

    def test_evaluation_fields(self, provider, writer, evaluations):
        process_ffe_configuration(_config(HOLDOUT_FEATURES))

        _evaluate(subject="user-9")

        evaluation = evaluations[0]
        assert (evaluation.flag_key, evaluation.variant, evaluation.allocation_key) == ("flag", "on", "alloc")
        assert evaluation.targeting_key == "user-9"
        assert evaluation.details.value == "on"
        with pytest.raises(TypeError):
            evaluation.features["holdout.key"] = "x"

    def test_error_evaluation_is_not_an_exposure(self, provider, writer, evaluations):
        process_ffe_configuration(_config())

        api.get_client().get_string_details("missing", "default", EvaluationContext(targeting_key="u"))

        assert [(e.is_exposure, e.should_send, dict(e.features)) for e in evaluations] == [(False, False, {})]

    @pytest.mark.parametrize(
        "error_code, metadata, expected",
        [
            (ErrorCode.GENERAL, {"allocation_key": "a", "__dd_do_log": True, "__dd_exposure_cache_hit": False}, False),
            (None, {"allocation_key": "a", "__dd_do_log": True}, False),
            (None, {"allocation_key": "a", "__dd_do_log": True, "__dd_exposure_cache_hit": False}, True),
        ],
    )
    def test_decision_reads_only_a_successful_stamped_exposure(self, error_code, metadata, expected):
        details = FlagEvaluationDetails(flag_key="f", value="on", variant="on", error_code=error_code)
        details.flag_metadata = metadata
        evaluation = ExposureEvaluation(details, None)

        assert evaluation.should_send is expected
        assert evaluation.is_cache_hit is False

    def test_callback_gets_the_subject_a_before_hook_set(self, provider, writer, evaluations):
        class SetSubject(Hook):
            def before(self, hook_context, hints):
                return EvaluationContext(targeting_key="from-before-hook", attributes={"tier": "gold"})

        api.add_hooks([SetSubject()])
        process_ffe_configuration(_config())

        api.get_client().get_string_details("flag", "default", EvaluationContext())

        evaluation = evaluations[0]
        assert (evaluation.targeting_key, evaluation.should_send) == ("from-before-hook", True)
        assert evaluation.evaluation_context.attributes == {"tier": "gold"}
        assert writer.enqueue.call_args[0][0]["subject"]["id"] == "from-before-hook"

    def test_rejects_a_non_callable_callback(self):
        with pytest.raises(TypeError):
            ExposureHook(None)


class TestEvaluationCountFeatures:
    def _rows(self, metadata_list):
        writer = FlagEvaluationWriter(interval=10.0)
        hook = FlagEvalEVPHook(writer)
        for metadata in metadata_list:
            details = mock.Mock(flag_metadata=metadata, variant="on", error_code=None)
            hook_context = mock.Mock(flag_key="flag", evaluation_context=EvaluationContext(targeting_key="u"))
            hook.finally_after(hook_context, details, {})
        with mock.patch.object(writer, "_send_payload") as send:
            writer.periodic()
        return [row for call in send.call_args_list for row in json.loads(call[0][0])["flagEvaluations"]]

    @staticmethod
    def _metadata(value):
        return {
            "allocation_key": "alloc",
            "__dd_evaluation_feature.k": value,
            "__dd_has_evaluation_features": True,
            "__dd_feature.hook_only": "h",
        }

    def test_evaluation_features_split_buckets_and_appear_on_rows(self):
        rows = self._rows([self._metadata("a"), self._metadata("a"), self._metadata("b")])

        assert sorted((row["features"]["k"], row["evaluation_count"]) for row in rows) == [("a", 2), ("b", 1)]
        assert all(row["features"].keys() == {"k"} for row in rows)

    def test_evaluation_features_split_degraded_buckets(self):
        with mock.patch("ddtrace.internal.openfeature._flagevaluation_writer.PER_FLAG_CAP", 0):
            rows = self._rows([self._metadata("a"), self._metadata("b")])

        assert sorted(row["features"]["k"] for row in rows) == ["a", "b"]

    def test_rows_without_evaluation_features_have_no_features_field(self):
        rows = self._rows([{"allocation_key": "alloc", "__dd_feature.hook_only": "h"}])

        assert "features" not in rows[0]


# The interpreter warns about forking a multi-threaded process.
@pytest.mark.subprocess(err=None)
def test_exposure_cache_lock_is_released_in_a_forked_child():
    import os

    from ddtrace.openfeature import DataDogProvider

    lock = DataDogProvider()._exposure_cache_lock
    lock.acquire()
    pid = os.fork()
    if pid == 0:
        os._exit(0 if lock.acquire(timeout=1) else 1)
    _, status = os.waitpid(pid, 0)

    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
