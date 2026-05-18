import json
from pathlib import Path

from openfeature.evaluation_context import EvaluationContext
import pytest

from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.openfeature import DataDogProvider
from tests.utils import override_global_config


# Get fixtures directory path
FFE_SYSTEM_TEST_DATA_DIR = Path(__file__).parent / "ffe-system-test-data"
FIXTURES_DIR = FFE_SYSTEM_TEST_DATA_DIR / "evaluation-cases"
FLAGS_CONFIG_PATH = FFE_SYSTEM_TEST_DATA_DIR / "ufc-config.json"


def load_flags_config():
    with open(FLAGS_CONFIG_PATH, "r") as f:
        return json.load(f)


def load_fixture_test_cases(fixture_file):
    fixture_path = FIXTURES_DIR / fixture_file
    with open(fixture_path, "r") as f:
        return json.load(f)


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


# Load all fixture files and create test parameters
fixture_files = get_all_fixture_files()
all_test_cases = []

for fixture_file in fixture_files:
    test_cases = load_fixture_test_cases(fixture_file)
    for i, test_case in enumerate(test_cases):
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

    expected_value = expected_result.get("value")
    assert result.value == expected_value, (
        f"Fixture {fixture_file} test {test_id}: flag '{flag_key}' with "
        f"context (targetingKey='{targeting_key}', attributes={attributes}) "
        f"returned {result.value}, expected {expected_value}"
    )
