"""Dependent evaluations and telemetry through the native evaluator and provider."""

import copy
from unittest import mock

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import Reason
import pytest

from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._flagevaluation_writer import EVAL_TIMESTAMP_METADATA_KEY
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.openfeature import DataDogProvider
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


def test_memoized_exposures_replay_successful_path_order_not_completion_order(evaluated):
    _, client, evaluations, exposures, _ = evaluated
    root = dependent_flag(
        rules=[
            [prerequisite("parent"), attribute("free")],
            [prerequisite("sibling"), prerequisite("parent"), attribute("pro")],
        ]
    )
    assert process_ffe_configuration(create_config(create_boolean_flag("parent"), create_boolean_flag("sibling"), root))

    assert (
        client.get_boolean_value("root", False, EvaluationContext(targeting_key="subject", attributes={"plan": "pro"}))
        is True
    )

    assert event_keys(evaluations) == ["parent", "sibling", "root"]
    assert exposure_keys(exposures) == ["sibling", "parent", "root"]


def test_rejected_nested_path_never_exposes_its_prerequisite(evaluated):
    _, client, evaluations, exposures, _ = evaluated
    child = create_boolean_flag("child")
    parent = dependent_flag("parent", rules=[[prerequisite("child"), attribute()]])
    root = dependent_flag(rules=[[prerequisite("parent", variants=["false"])]])
    assert process_ffe_configuration(create_config(child, parent, root))

    assert client.get_boolean_value("root", False, EvaluationContext(targeting_key="subject")) is True

    assert event_keys(evaluations) == ["child", "parent", "root"]
    assert exposure_keys(exposures) == ["parent", "root"]


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        ("missing", ErrorCode.FLAG_NOT_FOUND),
        ("disabled", ErrorCode.GENERAL),
        ("malformed", ErrorCode.PARSE_ERROR),
        ("unassigned", ErrorCode.GENERAL),
        ("cycle", ErrorCode.GENERAL),
        ("depth", ErrorCode.GENERAL),
    ],
)
def test_reached_dependency_failure_aborts_root_without_exposures(evaluated, failure, expected_error):
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
    assert details.error_code == expected_error
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


def test_dependency_events_use_root_snapshot_consent_and_timestamp(evaluated):
    _, client, evaluations, _, _ = evaluated
    config = create_config(create_boolean_flag("parent"), dependent_flag())
    config["observeFullEvaluationData"] = True
    assert process_ffe_configuration(config)
    replacement = copy.deepcopy(config)
    replacement["observeFullEvaluationData"] = False
    from ddtrace.internal.openfeature._native import resolve_flag

    def resolve_then_refresh(*args, **kwargs):
        result = resolve_flag(*args, **kwargs)
        assert process_ffe_configuration(replacement)
        return result

    with mock.patch("ddtrace.internal.openfeature._provider.resolve_flag", side_effect=resolve_then_refresh):
        details = client.get_boolean_details(
            "root", False, EvaluationContext(targeting_key="subject", attributes={"plan": "pro"})
        )

    assert details.value is True
    for call in evaluations.call_args_list:
        event = call.args[0]
        assert event.observe_full_evaluation_data is True
        assert event.attrs == {"plan": "pro"}
        assert event.eval_time_ms == details.flag_metadata[EVAL_TIMESTAMP_METADATA_KEY]


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


@pytest.mark.parametrize("operator", ["ONE_OF", "NOT_ONE_OF"])
def test_empty_prerequisite_variant_list_rejects_only_its_flag(evaluated, operator):
    _, client, evaluations, exposures, _ = evaluated
    condition = prerequisite(operator=operator)
    condition["value"] = []
    assert process_ffe_configuration(create_config(create_boolean_flag("parent"), dependent_flag(rules=[[condition]])))

    details = client.get_boolean_details("root", True, EvaluationContext(targeting_key="subject"))

    assert details.value is True
    assert details.error_code == ErrorCode.PARSE_ERROR
    assert event_keys(evaluations) == ["root"]
    assert not exposures.called
    assert client.get_boolean_value("parent", False) is True


def test_prerequisite_metric_failure_does_not_change_assignment_or_exposures(evaluated):
    _, client, _, exposures, metrics = evaluated
    assert process_ffe_configuration(create_config(create_boolean_flag("parent"), dependent_flag()))
    metrics.side_effect = RuntimeError("metrics unavailable")

    assert client.get_boolean_value("root", False, EvaluationContext(targeting_key="subject")) is True
    assert exposure_keys(exposures) == ["parent", "root"]
