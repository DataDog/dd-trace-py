import json
from pathlib import Path
from unittest import mock

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode
import pytest

from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.internal.openfeature._span_enrichment import METADATA_DO_LOG
from ddtrace.internal.openfeature._span_enrichment import METADATA_SERIAL_ID
from ddtrace.internal.openfeature._split_features import METADATA_EVALUATION_FEATURE_PREFIX
from ddtrace.internal.openfeature._split_features import METADATA_EXPOSURE_FEATURE_PREFIX
from ddtrace.internal.openfeature._split_features import METADATA_FEATURE_PREFIX
from ddtrace.internal.openfeature._split_features import features_with_prefix
from ddtrace.openfeature import DataDogProvider
from tests.openfeature.conftest import set_openfeature_provider
from tests.utils import override_global_config


# Get fixtures directory path
FFE_SYSTEM_TEST_DATA_DIR = Path(__file__).parent / "ffe-system-test-data"
FIXTURES_DIR = FFE_SYSTEM_TEST_DATA_DIR / "evaluation-cases"
FLAGS_CONFIG_PATH = FFE_SYSTEM_TEST_DATA_DIR / "ufc-config.json"


def load_flags_config():
    with open(FLAGS_CONFIG_PATH) as f:
        return json.load(f)


def load_fixture_test_cases(fixture_file):
    fixture_path = FIXTURES_DIR / fixture_file
    with open(fixture_path) as f:
        return json.load(f)


def applies_to_python(item):
    if "_include" in item:
        return any(entry["platform"] == "python" for entry in item["_include"])
    return not any(entry["platform"] == "python" for entry in item.get("_skip", []))


def get_all_fixture_files():
    return sorted(f.name for f in FIXTURES_DIR.glob("*.json"))


def variation_type_to_method(provider, variation_type):
    mapping = {
        "BOOLEAN": provider.resolve_boolean_details,
        "STRING": provider.resolve_string_details,
        "INTEGER": provider.resolve_integer_details,
        "NUMERIC": provider.resolve_float_details,
        "JSON": provider.resolve_object_details,
    }
    return mapping.get(variation_type)


def assert_selected_metadata(result, expected):
    if "serialId" in expected:
        assert result.flag_metadata.get(METADATA_SERIAL_ID) == expected["serialId"]
    if "doLog" in expected:
        assert result.flag_metadata.get(METADATA_DO_LOG) == expected["doLog"]
    prefixes = {
        "HOOK": METADATA_FEATURE_PREFIX,
        "EXPOSURE": METADATA_EXPOSURE_FEATURE_PREFIX,
        "EVALUATION": METADATA_EVALUATION_FEATURE_PREFIX,
    }
    for destination, features in expected.get("features", {}).items():
        assert features_with_prefix(result.flag_metadata, prefixes[destination]) == features


# Load all fixture files and create test parameters
fixture_files = get_all_fixture_files()
all_test_cases = []

for fixture_file in fixture_files:
    test_cases = load_fixture_test_cases(fixture_file)
    for i, test_case in enumerate(test_cases):
        if not applies_to_python(test_case):
            continue
        test_id = f"{fixture_file.replace('.json', '')}_{i}_{test_case.get('targetingKey', 'no_key')}"
        all_test_cases.append((fixture_file, test_case, test_id))

assert all_test_cases, f"No FFE JSON fixtures found in {FIXTURES_DIR}"


@pytest.fixture
def provider():
    with override_global_config({"experimental_flagging_provider_enabled": True}):
        yield DataDogProvider()


@pytest.fixture(autouse=True)
def clear_config():
    _set_ffe_config(None)
    yield
    _set_ffe_config(None)


@pytest.fixture(scope="module")
def flags_config():
    return load_flags_config()


@pytest.mark.parametrize("fixture_file,test_case,test_id", all_test_cases, ids=[tc[2] for tc in all_test_cases])
def test_fixture_case(provider, flags_config, fixture_file, test_case, test_id):
    process_ffe_configuration(flags_config)

    flag_key = test_case["flag"]
    variation_type = test_case["variationType"]
    default_value = test_case["defaultValue"]
    targeting_key = test_case.get("targetingKey")
    attributes = test_case.get("attributes", {})
    expected_result = test_case["result"]

    evaluation_context = EvaluationContext(targeting_key=targeting_key, attributes=attributes)
    resolve_method = variation_type_to_method(provider, variation_type)
    assert resolve_method is not None, f"Unknown variationType: {variation_type}"

    result = resolve_method(flag_key, default_value, evaluation_context)

    assert_selected_metadata(result, expected_result)
    expected_value = expected_result.get("value")
    assert result.value == expected_value, (
        f"Fixture {fixture_file} test {test_id}: flag '{flag_key}' with "
        f"context (targetingKey='{targeting_key}', attributes={attributes}) "
        f"returned {result.value}, expected {expected_value}"
    )
    assert result.reason is not None
    assert result.reason.value == expected_result["reason"], (
        f"Fixture {fixture_file} test {test_id}: flag '{flag_key}' returned reason "
        f"{result.reason.value}, expected {expected_result['reason']}"
    )
    if "errorCode" in expected_result:
        assert result.error_code == ErrorCode(expected_result["errorCode"]), (
            f"Fixture {fixture_file} test {test_id}: flag '{flag_key}' returned error code "
            f"{result.error_code}, expected {expected_result['errorCode']}"
        )


DEPENDENT_SUITE = FFE_SYSTEM_TEST_DATA_DIR / "evaluation-suites" / "dependent-flags"
dependent_cases = [
    case
    for case_file in sorted((DEPENDENT_SUITE / "cases").glob("*.json"))
    for case in json.loads(case_file.read_text())
    if applies_to_python(case)
]
assert len(dependent_cases) == 16, "Canonical dependent-flag coverage changed; review the fixture snapshot"


def assert_event_expectations(events, matchers, exact):
    # Every received event belongs to the first matching applicable matcher.
    active = [matcher for matcher in matchers if applies_to_python(matcher)]
    fields = [{key: value for key, value in matcher.items() if not key.startswith("_")} for matcher in active]
    counts = [0] * len(active)
    unmatched = []
    for event in events:
        for index, expected in enumerate(fields):
            if all(event.get(key) == value for key, value in expected.items()):
                counts[index] += 1
                break
        else:
            unmatched.append(event)
    assert counts == [matcher.get("_count", 1) for matcher in active], (active, counts, events)
    if exact:
        assert not unmatched, unmatched


@pytest.mark.parametrize("case", dependent_cases, ids=[case["targetingKey"] for case in dependent_cases])
def test_dependent_fixture_case(case):
    # Every case has an isolated cache and goes through the public client so final
    # provider hooks run, including error evaluations and exposure finalization.
    api.set_evaluation_context(EvaluationContext())
    with override_global_config({"experimental_flagging_provider_enabled": True}):
        provider = DataDogProvider()
    set_openfeature_provider(provider)
    try:
        assert process_ffe_configuration(json.loads((DEPENDENT_SUITE / "ufc-config.json").read_text()))
        context = EvaluationContext(targeting_key=case.get("targetingKey"), attributes=case.get("attributes", {}))
        client = api.get_client()
        method = {
            "BOOLEAN": client.get_boolean_details,
            "STRING": client.get_string_details,
            "INTEGER": client.get_integer_details,
            "NUMERIC": client.get_float_details,
            "JSON": client.get_object_details,
        }[case["variationType"]]
        with (
            mock.patch.object(provider._flag_eval_evp_writer, "enqueue") as evaluations,
            mock.patch("ddtrace.internal.openfeature._provider.get_exposure_writer") as exposures,
        ):
            result = method(case["flag"], case["defaultValue"], context)
        expected = case["result"]
        assert result.value == expected["value"]
        assert result.reason.value == expected["reason"]
        assert result.error_code == (ErrorCode(expected["errorCode"]) if expected.get("errorCode") else None)
        assert_selected_metadata(result, expected)
        if "expectations" in case:
            expectations = case["expectations"]
            exact = expectations.get("noUnmatchedEvents", False)
            exposure_events = [
                {
                    "flag": call.args[0]["flag"]["key"],
                    "allocation": call.args[0]["allocation"]["key"],
                    "variant": call.args[0]["variant"]["key"],
                    "serialId": call.args[0].get("serial_id"),
                }
                for call in exposures.return_value.enqueue.call_args_list
            ]
            evaluation_events = [
                {
                    "flag": call.args[0].flag_key,
                    "allocation": call.args[0].allocation_key or None,
                    "variant": call.args[0].variant or None,
                    "errorCode": call.args[0].error_code or None,
                }
                for call in evaluations.call_args_list
            ]
            if "exposures" in expectations:
                assert_event_expectations(exposure_events, expectations["exposures"], exact)
            if "evaluationEvents" in expectations:
                assert_event_expectations(evaluation_events, expectations["evaluationEvents"], exact)
    finally:
        api.shutdown()
        api.set_evaluation_context(EvaluationContext())


@pytest.mark.parametrize(
    "events,matchers",
    [
        ([{"flag": "a"}], [{"flag": "a", "_count": 2}]),
        ([{"flag": "a"}], [{"flag": "b"}]),
        ([{"flag": "a"}, {"flag": "b"}], [{"flag": "a"}]),
        ([{"flag": "a"}], [{"flag": "a"}, {"flag": "a"}]),
    ],
)
def test_event_expectations_reject_missing_extra_or_reused_events(events, matchers):
    with pytest.raises(AssertionError):
        assert_event_expectations(events, matchers, exact=True)


def test_event_expectations_apply_platform_controls_and_multiplicity():
    assert_event_expectations(
        [{"flag": "a"}, {"flag": "a"}],
        [
            {"flag": "other", "_skip": [{"platform": "python"}]},
            {"flag": "a", "_count": 2, "_include": [{"platform": "python"}]},
        ],
        exact=True,
    )
