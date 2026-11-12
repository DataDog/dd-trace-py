"""Dependent evaluations and telemetry through the native evaluator and provider."""

import copy
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import mock

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import FlagEvaluationOptions
from openfeature.flag_evaluation import Reason
from openfeature.hook import Hook
import pytest

from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._flagevaluation_writer import EVAL_TIMESTAMP_METADATA_KEY
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.internal.openfeature._native import resolve_flag
from ddtrace.openfeature import DataDogProvider
from ddtrace.openfeature import ExposureHook
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config
from tests.openfeature.conftest import set_openfeature_provider
from tests.utils import override_global_config


def prerequisite(key="parent", variants=None, operator="ONE_OF"):
    return {"flagEvaluation": {"key": key}, "operator": operator, "value": variants or ["true"]}


def attribute(value="pro"):
    return {"attribute": "plan", "operator": "ONE_OF", "value": [value]}


def dependent_flag(key="root", rules=None):
    flag = create_boolean_flag(key, default_value=False)
    flag["allocations"].insert(
        0,
        {
            "key": key + "-targeting",
            "rules": [{"conditions": conditions} for conditions in (rules or [[prerequisite()]])],
            "splits": [{"variationKey": "true", "shards": [], "serialId": 42}],
            "doLog": True,
        },
    )
    return flag


@pytest.fixture
def evaluated():
    api.set_evaluation_context(EvaluationContext())
    _set_ffe_config(None)
    with override_global_config({"experimental_flagging_provider_enabled": True}):
        provider = DataDogProvider()
    set_openfeature_provider(provider)
    client = api.get_client()
    writer = provider._flag_eval_evp_writer
    with (
        mock.patch.object(writer, "enqueue") as evaluations,
        mock.patch("ddtrace.internal.openfeature._provider.get_exposure_writer") as exposures,
        mock.patch.object(provider._flag_eval_metrics, "record") as metrics,
    ):
        yield provider, client, evaluations, exposures.return_value.enqueue, metrics
    api.shutdown()
    api.set_evaluation_context(EvaluationContext())
    _set_ffe_config(None)


def event_keys(evaluations):
    return [call.args[0].flag_key for call in evaluations.call_args_list]


def exposure_keys(exposures):
    return [call.args[0]["flag"]["key"] for call in exposures.call_args_list]


@pytest.mark.parametrize("operator, expected", [("ONE_OF", True), ("NOT_ONE_OF", False)])
def test_prerequisite_matches_variant_key_not_boolean_value(evaluated, operator, expected):
    _, client, evaluations, exposures, _ = evaluated
    parent = create_boolean_flag("parent", default_value=False)
    root = dependent_flag(rules=[[prerequisite(variants=["false"], operator=operator)]])
    assert process_ffe_configuration(create_config(parent, root))

    details = client.get_boolean_details("root", False, EvaluationContext(targeting_key="subject"))

    assert details.value is expected
    assert details.error_code is None
    assert event_keys(evaluations) == ["parent", "root"]
    assert set(exposure_keys(exposures)) == ({"parent", "root"} if expected else {"root"})


@pytest.mark.parametrize(
    "conditions, reached",
    [([attribute(), prerequisite()], False), ([prerequisite(), attribute()], True)],
)
def test_authored_short_circuit_order_controls_events_and_exposures(evaluated, conditions, reached):
    _, client, evaluations, exposures, _ = evaluated
    assert process_ffe_configuration(create_config(create_boolean_flag("parent"), dependent_flag(rules=[conditions])))

    details = client.get_boolean_details(
        "root", True, EvaluationContext(targeting_key="subject", attributes={"plan": "free"})
    )

    assert details.value is False
    assert details.error_code is None
    assert event_keys(evaluations) == (["parent", "root"] if reached else ["root"])
    assert exposure_keys(exposures) == ["root"]


def test_expanded_rules_memoize_prerequisite_but_keep_its_successful_exposure(evaluated):
    _, client, evaluations, exposures, metrics = evaluated
    root = dependent_flag(rules=[[prerequisite(), attribute("free")], [prerequisite(), attribute("pro")]])
    assert process_ffe_configuration(create_config(create_boolean_flag("parent"), root))
    context = EvaluationContext(targeting_key="subject", attributes={"plan": "pro"})

    for _ in range(2):
        assert client.get_boolean_value("root", False, context) is True

    assert event_keys(evaluations) == ["parent", "root", "parent", "root"]
    assert [call.kwargs["flag_key"] for call in metrics.call_args_list] == ["parent", "root", "parent", "root"]
    assert exposure_keys(exposures) == ["parent", "root"]
    root_exposure = exposures.call_args_list[1].args[0]
    assert root_exposure["serial_id"] == 42


def test_rejected_nested_path_never_exposes_its_prerequisite(evaluated):
    _, client, evaluations, exposures, _ = evaluated
    child = create_boolean_flag("child")
    parent = dependent_flag("parent", rules=[[prerequisite("child"), attribute()]])
    root = dependent_flag(rules=[[prerequisite("parent", variants=["false"])]])
    assert process_ffe_configuration(create_config(child, parent, root))

    assert client.get_boolean_value("root", False, EvaluationContext(targeting_key="subject")) is True

    assert event_keys(evaluations) == ["child", "parent", "root"]
    assert exposure_keys(exposures) == ["parent", "root"]


@pytest.mark.parametrize("failure", ["missing", "disabled", "malformed", "unassigned", "cycle", "depth"])
def test_reached_dependency_failure_aborts_root_without_exposures(evaluated, failure):
    _, client, evaluations, exposures, _ = evaluated
    parent = create_boolean_flag("parent")
    root = dependent_flag()
    config = create_config(parent, root)
    if failure == "missing":
        del config["flags"]["parent"]
    elif failure == "disabled":
        parent["enabled"] = False
    elif failure == "malformed":
        parent["variationType"] = "UNKNOWN"
    elif failure == "unassigned":
        parent["allocations"] = []
    elif failure == "cycle":
        config["flags"]["parent"] = dependent_flag("parent", [[prerequisite("root")]])
    else:
        config["flags"]["parent"] = dependent_flag("parent", [[prerequisite("child")]])
        config["flags"]["child"] = create_boolean_flag("child")
        config["evaluatorParams"] = {"maxDependencyDepth": 1}
    assert process_ffe_configuration(config)

    details = client.get_boolean_details("root", True, EvaluationContext(targeting_key="subject"))

    assert details.value is True  # The runtime default, not the root's false fallback.
    assert details.reason == Reason.ERROR
    assert details.error_code == ErrorCode.GENERAL
    assert "parent" in event_keys(evaluations)
    assert event_keys(evaluations)[-1] == "root"
    assert exposures.call_count == 0
    parent_event = next(call.args[0] for call in evaluations.call_args_list if call.args[0].flag_key == "parent")
    assert parent_event.runtime_default
    assert parent_event.error_code


def test_unreached_missing_dependency_does_not_fail_root(evaluated):
    _, client, evaluations, exposures, _ = evaluated
    root = dependent_flag(rules=[[attribute(), prerequisite()]])
    assert process_ffe_configuration(create_config(root))

    details = client.get_boolean_details("root", True, EvaluationContext(targeting_key="subject"))

    assert details.value is False
    assert details.error_code is None
    assert event_keys(evaluations) == ["root"]
    assert exposure_keys(exposures) == ["root"]


def test_prerequisite_do_log_and_telemetry_failure_do_not_change_assignment(evaluated):
    _, client, evaluations, exposures, _ = evaluated
    parent = create_boolean_flag("parent")
    parent["allocations"][0]["doLog"] = False
    assert process_ffe_configuration(create_config(parent, dependent_flag()))
    evaluations.side_effect = RuntimeError("telemetry unavailable")

    assert client.get_boolean_value("root", False, EvaluationContext(targeting_key="subject")) is True
    assert exposure_keys(exposures) == ["root"]


@pytest.mark.parametrize("consent", [True, False])
def test_dependency_events_use_root_snapshot_consent_and_timestamp(evaluated, consent):
    _, client, evaluations, _, _ = evaluated
    config = create_config(create_boolean_flag("parent"), dependent_flag())
    config["observeFullEvaluationData"] = consent
    assert process_ffe_configuration(config)
    replacement = copy.deepcopy(config)
    replacement["observeFullEvaluationData"] = not consent

    def resolve_then_refresh(*args, **kwargs):
        result = resolve_flag(*args, **kwargs)
        assert process_ffe_configuration(replacement)
        return result

    with mock.patch("ddtrace.internal.openfeature._provider.resolve_flag", side_effect=resolve_then_refresh):
        details = client.get_boolean_details(
            "root", False, EvaluationContext(targeting_key="subject", attributes={"plan": "pro"})
        )

    assert details.value is True
    assert event_keys(evaluations) == ["parent", "root"]
    for call in evaluations.call_args_list:
        event = call.args[0]
        assert event.observe_full_evaluation_data is consent
        assert event.attrs == ({"plan": "pro"} if consent else {})
        assert event.eval_time_ms == details.flag_metadata[EVAL_TIMESTAMP_METADATA_KEY]


def test_independent_flag_with_older_native_resolution_details(evaluated):
    _, client, evaluations, exposures, metrics = evaluated
    assert process_ffe_configuration(create_config(create_boolean_flag("root")))

    def resolve_without_dependencies(*args, **kwargs):
        result = resolve_flag(*args, **kwargs)
        return SimpleNamespace(
            **{
                name: getattr(result, name)
                for name in (
                    "value",
                    "error_code",
                    "error_message",
                    "reason",
                    "variant",
                    "allocation_key",
                    "flag_metadata",
                    "do_log",
                    "serial_id",
                )
            }
        )

    with mock.patch("ddtrace.internal.openfeature._provider.resolve_flag", side_effect=resolve_without_dependencies):
        details = client.get_boolean_details("root", False, EvaluationContext(targeting_key="subject"))

    assert details.value is True
    assert details.error_code is None
    assert details.variant == "true"
    assert event_keys(evaluations) == ["root"]
    assert exposure_keys(exposures) == ["root"]
    assert [call.kwargs["flag_key"] for call in metrics.call_args_list] == ["root"]


def test_direct_disabled_flag_retains_disabled_reason(evaluated):
    _, client, evaluations, exposures, _ = evaluated
    assert process_ffe_configuration(create_config(create_boolean_flag("root", enabled=False)))

    details = client.get_boolean_details("root", True, EvaluationContext(targeting_key="subject"))

    assert details.value is True
    assert details.reason == Reason.DISABLED
    assert details.error_code is None
    assert event_keys(evaluations) == ["root"]
    assert not exposures.called


def test_root_type_mismatch_never_evaluates_prerequisites(evaluated):
    _, client, evaluations, exposures, _ = evaluated
    assert process_ffe_configuration(create_config(create_boolean_flag("parent"), dependent_flag()))

    details = client.get_string_details("root", "runtime-default", EvaluationContext(targeting_key="subject"))

    assert details.value == "runtime-default"
    assert details.error_code == ErrorCode.TYPE_MISMATCH
    assert event_keys(evaluations) == ["root"]
    assert not exposures.called


@pytest.mark.parametrize("operator,matched", [("ONE_OF", False), ("NOT_ONE_OF", True)])
def test_empty_prerequisite_variant_list_still_evaluates_the_prerequisite(evaluated, operator, matched):
    _, client, evaluations, exposures, _ = evaluated
    condition = prerequisite(operator=operator)
    condition["value"] = []
    assert process_ffe_configuration(create_config(create_boolean_flag("parent"), dependent_flag(rules=[[condition]])))

    details = client.get_boolean_details("root", True, EvaluationContext(targeting_key="subject"))

    assert details.value is matched
    assert details.error_code is None
    assert event_keys(evaluations) == ["parent", "root"]
    assert exposure_keys(exposures) == (["parent", "root"] if matched else ["root"])
    assert client.get_boolean_value("parent", False) is True


def test_prerequisite_metric_failure_does_not_change_assignment_or_exposures(evaluated):
    _, client, _, exposures, metrics = evaluated
    assert process_ffe_configuration(create_config(create_boolean_flag("parent"), dependent_flag()))
    metrics.side_effect = RuntimeError("metrics unavailable")

    assert client.get_boolean_value("root", False, EvaluationContext(targeting_key="subject")) is True
    assert exposure_keys(exposures) == ["parent", "root"]


class RejectAfter(Hook):
    def after(self, hook_context, details, hints):
        raise RuntimeError("application rejected the evaluation")


@pytest.mark.parametrize("dependent", [False, True])
def test_later_hook_failure_discards_graph_exposures_and_cache_admission(evaluated, dependent):
    provider, client, evaluations, exposures, _ = evaluated
    root = dependent_flag() if dependent else create_boolean_flag("root")
    assert process_ffe_configuration(create_config(create_boolean_flag("parent"), root))
    expected_keys = ["parent", "root"] if dependent else ["root"]
    context = EvaluationContext(targeting_key="subject")

    failed = client.get_boolean_details("root", False, context, FlagEvaluationOptions(hooks=[RejectAfter()]))

    assert failed.reason == Reason.ERROR
    assert failed.value is False
    assert failed.error_code == ErrorCode.GENERAL
    assert event_keys(evaluations) == expected_keys
    assert not exposures.called
    assert not provider._exposure_cache

    assert client.get_boolean_value("root", False, context) is True
    assert exposure_keys(exposures) == expected_keys
    assert client.get_boolean_value("root", False, context) is True
    assert exposure_keys(exposures) == expected_keys


def test_reentrant_evaluation_commits_independently_of_failed_outer_hook(evaluated):
    provider, client, evaluations, exposures, _ = evaluated
    assert process_ffe_configuration(
        create_config(create_boolean_flag("parent"), dependent_flag(), dependent_flag("inner"))
    )
    context = EvaluationContext(targeting_key="subject")

    class EvaluateThenReject(Hook):
        def after(self, hook_context, details, hints):
            assert client.get_boolean_value("inner", False, context) is True
            raise RuntimeError("reject outer evaluation")

    result = client.get_boolean_details("root", False, context, FlagEvaluationOptions(hooks=[EvaluateThenReject()]))
    assert result.reason == Reason.ERROR
    assert exposure_keys(exposures) == ["parent", "inner"]
    assert provider._exposure_cache.get(("root", "subject"), None) is None
    assert client.get_boolean_value("root", False, context) is True
    assert exposure_keys(exposures) == ["parent", "inner", "root"]


def test_before_hook_failure_does_not_discard_a_reentrant_outer_batch(evaluated):
    _, client, _, exposures, _ = evaluated
    assert process_ffe_configuration(create_config(create_boolean_flag("parent"), dependent_flag()))
    context = EvaluationContext(targeting_key="subject")

    class RejectBefore(Hook):
        def before(self, hook_context, hints):
            raise RuntimeError("before hook rejected the evaluation")

    class EvaluateRejectedCall(Hook):
        def after(self, hook_context, details, hints):
            result = client.get_boolean_details("root", False, context, FlagEvaluationOptions(hooks=[RejectBefore()]))
            assert result.reason == Reason.ERROR

    result = client.get_boolean_details("root", False, context, FlagEvaluationOptions(hooks=[EvaluateRejectedCall()]))
    assert result.value is True
    assert exposure_keys(exposures) == ["parent", "root"]


def test_depth_rejected_prerequisite_has_its_own_error_event(evaluated):
    _, client, evaluations, exposures, _ = evaluated
    config = create_config(
        create_boolean_flag("leaf"), dependent_flag("parent", [[prerequisite("leaf")]]), dependent_flag()
    )
    config["evaluatorParams"] = {"maxDependencyDepth": 1}
    assert process_ffe_configuration(config)

    result = client.get_boolean_details("root", True, EvaluationContext(targeting_key="subject"))

    assert result.reason == Reason.ERROR
    assert result.value is True
    assert event_keys(evaluations) == ["leaf", "parent", "root"]
    assert all(call.args[0].error_code for call in evaluations.call_args_list)
    assert not exposures.called


@pytest.mark.parametrize("do_log", [True, False])
def test_selected_prerequisite_fallback_uses_the_ordinary_exposure_gate(evaluated, do_log):
    _, client, evaluations, exposures, _ = evaluated
    parent = create_boolean_flag("parent", default_value=False)
    parent["allocations"][0]["doLog"] = do_log
    assert process_ffe_configuration(create_config(parent, dependent_flag(rules=[[prerequisite(variants=["false"])]])))

    assert client.get_boolean_value("root", False, EvaluationContext(targeting_key="subject")) is True
    assert event_keys(evaluations) == ["parent", "root"]
    assert exposure_keys(exposures) == (["parent", "root"] if do_log else ["root"])


def test_overlapping_evaluations_commit_only_the_successful_graph(evaluated):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    _, client, evaluations, exposures, _ = evaluated
    assert process_ffe_configuration(
        create_config(create_boolean_flag("parent"), dependent_flag("success"), dependent_flag("failure"))
    )
    barrier = Barrier(2)

    class HoldThenReject(Hook):
        def after(self, hook_context, details, hints):
            barrier.wait(timeout=5)
            if hook_context.flag_key == "failure":
                raise RuntimeError("reject one overlapping evaluation")

    def evaluate(key):
        return client.get_boolean_details(
            key, False, EvaluationContext(targeting_key="subject"), FlagEvaluationOptions(hooks=[HoldThenReject()])
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        successful = executor.submit(evaluate, "success")
        failed = executor.submit(evaluate, "failure")
        assert successful.result(timeout=10).value is True
        assert failed.result(timeout=10).reason == Reason.ERROR
    assert sorted(exposure_keys(exposures)) == ["parent", "success"]
    assert event_keys(evaluations).count("parent") == 2


def test_async_hook_failure_discards_exposures_without_poisoning_a_retry(evaluated):
    import asyncio

    provider, client, evaluations, exposures, _ = evaluated
    assert process_ffe_configuration(create_config(create_boolean_flag("parent"), dependent_flag()))
    context = EvaluationContext(targeting_key="subject")

    async def evaluate():
        failed = await client.get_boolean_details_async(
            "root", False, context, FlagEvaluationOptions(hooks=[RejectAfter()])
        )
        assert failed.reason == Reason.ERROR
        assert failed.value is False
        assert not exposures.called
        assert not provider._exposure_cache
        result = await client.get_boolean_details_async("root", False, context)
        assert result.value is True

    asyncio.run(evaluate())
    assert event_keys(evaluations) == ["parent", "root", "parent", "root"]
    assert exposure_keys(exposures) == ["parent", "root"]


def test_cached_exposure_does_not_copy_application_attributes(evaluated):
    _, client, _, exposures, _ = evaluated
    assert process_ffe_configuration(create_config(create_boolean_flag("root")))
    context = EvaluationContext(targeting_key="subject", attributes={"plan": "pro"})
    assert client.get_boolean_value("root", False, context) is True
    with mock.patch("copy.deepcopy", side_effect=AssertionError("cached exposures need no snapshot")) as snapshot:
        assert client.get_boolean_value("root", False, context) is True
        snapshot.assert_not_called()
    assert exposure_keys(exposures) == ["root"]


@pytest.mark.parametrize("dependent", [False, True])
def test_successful_after_hook_cannot_mutate_staged_exposure_subject(evaluated, dependent):
    _, client, _, exposures, _ = evaluated
    root = dependent_flag() if dependent else create_boolean_flag("root")
    assert process_ffe_configuration(create_config(create_boolean_flag("parent"), root))
    context = EvaluationContext(targeting_key="subject", attributes={"plan": "pro"})

    class MutateContext(Hook):
        def after(self, hook_context, details, hints):
            hook_context.evaluation_context.attributes["plan"] = "changed"

    result = client.get_boolean_details("root", False, context, FlagEvaluationOptions(hooks=[MutateContext()]))
    assert result.value is True
    assert exposure_keys(exposures) == (["parent", "root"] if dependent else ["root"])
    for call in exposures.call_args_list:
        assert call.args[0]["subject"]["attributes"] == {"plan": "pro"}


@pytest.mark.parametrize("reject", [False, True])
def test_prerequisite_split_features_and_public_exposure_hook(evaluated, reject):
    provider, client, _, exposures, _ = evaluated
    parent = create_boolean_flag("parent")
    root = dependent_flag()
    for flag, value in ((parent, "parent-layer"), (root, "root-layer")):
        flag["allocations"][0]["splits"][0]["features"] = [
            {"key": "layer", "value": value, "destinations": ["HOOK", "EXPOSURE"]}
        ]
    assert process_ffe_configuration(create_config(parent, root))
    received = []
    api.add_hooks([ExposureHook(received.append)])
    try:
        result = client.get_boolean_details(
            "root",
            False,
            EvaluationContext(targeting_key="subject"),
            FlagEvaluationOptions(hooks=[RejectAfter()] if reject else []),
        )
        assert [evaluation.flag_key for evaluation in received] == ["root"]
        assert received[0].should_send is (not reject)
        if reject:
            assert result.reason == Reason.ERROR
            assert not exposures.called
            assert not provider._exposure_cache
            assert client.get_boolean_value("root", False, EvaluationContext(targeting_key="subject")) is True
        else:
            assert result.value is True
        assert exposure_keys(exposures) == ["parent", "root"]
        assert [call.args[0]["features"] for call in exposures.call_args_list] == [
            {"layer": "parent-layer"},
            {"layer": "root-layer"},
        ]
        assert [evaluation.flag_key for evaluation in received] == (["root", "root"] if reject else ["root"])
        assert dict(received[-1].features) == {"layer": "root-layer"}
    finally:
        api.clear_hooks()


@pytest.mark.parametrize("consent", [False, True])
@pytest.mark.parametrize("do_log", [False, True])
@pytest.mark.parametrize("accepted", [False, True])
def test_prerequisite_evaluation_features_preserve_count_buckets(evaluated, consent, do_log, accepted):
    provider, client, evaluations, exposures, _ = evaluated
    parent = create_boolean_flag("parent")
    parent["allocations"][0]["doLog"] = do_log
    root = dependent_flag(rules=[[prerequisite(), attribute()]] if not accepted else None)
    context = EvaluationContext(targeting_key="subject", attributes={"plan": "free"})
    for layer in ("first", "second", "first"):
        parent["allocations"][0]["splits"][0]["features"] = [
            {"key": "layer", "value": layer, "destinations": ["EVALUATION", "EXPOSURE"]},
            {"key": "weight", "value": 2.5, "destinations": ["EVALUATION"]},
            {"key": "enabled", "value": True, "destinations": ["EVALUATION"]},
            {"key": "hook_only", "value": "private", "destinations": ["HOOK"]},
        ]
        config = create_config(parent, root)
        config["observeFullEvaluationData"] = consent
        assert process_ffe_configuration(config)
        result = client.get_boolean_details("root", False, context)
        assert result.value is accepted
        assert result.error_code is None

    parent_events = [call.args[0] for call in evaluations.call_args_list if call.args[0].flag_key == "parent"]
    assert [event.features for event in parent_events] == [
        {"layer": layer, "weight": 2.5, "enabled": True} for layer in ("first", "second", "first")
    ]
    for event in parent_events:
        assert event.attrs == ({"plan": "free"} if consent else {})
        provider._flag_eval_evp_writer._aggregate(event)
    buckets = provider._flag_eval_evp_writer._full.values()
    assert sorted((entry.features["layer"], entry.count) for entry in buckets) == [("first", 2), ("second", 1)]
    assert ("parent" in exposure_keys(exposures)) is (accepted and do_log)


@pytest.mark.parametrize("dependent", [False, True])
def test_public_exposure_hook_cannot_mutate_queued_subject(evaluated, dependent):
    _, client, _, exposures, _ = evaluated
    root = dependent_flag() if dependent else create_boolean_flag("root")
    assert process_ffe_configuration(create_config(create_boolean_flag("parent"), root))
    attributes = {"plan": "pro", "age": 42, "ratio": 0.5, "enabled": True, "missing": None}
    context = EvaluationContext(targeting_key="subject", attributes=dict(attributes))

    def mutate(evaluation):
        evaluation.evaluation_context.attributes.clear()
        evaluation.evaluation_context.attributes["plan"] = "changed"

    api.add_hooks([ExposureHook(mutate)])
    try:
        assert client.get_boolean_value("root", False, context) is True
        assert exposure_keys(exposures) == (["parent", "root"] if dependent else ["root"])
        for call in exposures.call_args_list:
            assert call.args[0]["subject"]["attributes"] == attributes
    finally:
        api.clear_hooks()


@pytest.mark.parametrize("dependencies", [0, 1, 5, 10])
@pytest.mark.parametrize("cold", [False, True])
def test_dependency_benchmark_exercises_telemetry_and_exposures(evaluated, dependencies, cold):
    # Load the scenario without bm's command-line registration; exercise its real
    # provider loop and verify that the timed workload includes every reached node.
    path = Path(__file__).parents[2] / "benchmarks/openfeature_flagevaluation/scenario.py"
    spec = importlib.util.spec_from_file_location("dependent_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"bm": SimpleNamespace(Scenario=object)}):
        spec.loader.exec_module(module)
    scenario = module.OpenFeatureFlagEvaluation()
    scenario.num_flags = 1
    scenario.num_users = 2
    scenario.num_context_fields = 10
    scenario.num_dependencies = dependencies
    scenario.split_features = True
    scenario.observe_full_evaluation_data = True
    scenario.cold_exposures = cold
    scenario.warm_exposures = not cold
    from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter

    with (
        mock.patch.object(FlagEvaluationWriter, "enqueue") as evaluations,
        mock.patch.object(module._DISCARDING_EXPOSURE_WRITER, "enqueue") as exposures,
    ):
        run = scenario._provider_resolution()
        loop = next(run)
        evaluations.reset_mock()
        exposures.reset_mock()
        loop(4)
        assert evaluations.call_count == 4 * (dependencies + 1)
        assert exposures.call_count == (4 * (dependencies + 1) if cold else 0)
        assert {call.args[0].flag_key for call in evaluations.call_args_list} == {
            "flag-0",
            *[f"prerequisite-{i}" for i in range(dependencies)],
        }
        run.close()
