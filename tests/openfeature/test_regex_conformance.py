import json
from pathlib import Path

from openfeature.evaluation_context import EvaluationContext
import pytest

from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.openfeature import DataDogProvider
from tests.utils import override_global_config


REGEX_CONFORMANCE_PATH = (
    Path(__file__).parent / "ffe-system-test-data" / "regex-conformance" / "targeting-regex-conformance.json"
)


with REGEX_CONFORMANCE_PATH.open() as f:
    REGEX_FIXTURE = json.load(f)

assert REGEX_FIXTURE["schema"] == "datadog.ffe.targeting-regex-conformance/v1"
assert REGEX_FIXTURE["schemaVersion"] == 1
assert REGEX_FIXTURE["contractVersion"] == "targeting-regex-v2"
REGEX_CASES = REGEX_FIXTURE["cases"]
assert len(REGEX_CASES) == 75
assert len({test_case["id"] for test_case in REGEX_CASES}) == 75


def _rust_rules_based_expectation(test_case):
    engine_expectation = test_case.get("engineExpectations", {}).get("rustRulesBased")
    if engine_expectation is not None:
        return engine_expectation
    return {
        "compile": test_case["expectedCompile"],
        "match": test_case["expectedMatch"],
    }


def _regex_config(pattern):
    return {
        "format": "SERVER",
        "createdAt": "2026-01-01T00:00:00Z",
        "environment": {"name": "Regex conformance"},
        "flags": {
            "regex-conformance": {
                "key": "regex-conformance",
                "enabled": True,
                "variationType": "STRING",
                "variations": {
                    "matched": {
                        "key": "matched",
                        "value": "matched",
                    }
                },
                "allocations": [
                    {
                        "key": "regex-conformance",
                        "rules": [
                            {
                                "conditions": [
                                    {
                                        "attribute": "candidate",
                                        "operator": "MATCHES",
                                        "value": pattern,
                                    }
                                ]
                            }
                        ],
                        "splits": [{"variationKey": "matched", "shards": []}],
                        "doLog": False,
                    }
                ],
            }
        },
    }


@pytest.fixture
def provider():
    with override_global_config({"experimental_flagging_provider_enabled": True}):
        yield DataDogProvider()


@pytest.fixture(autouse=True)
def clear_config():
    _set_ffe_config(None)
    yield
    _set_ffe_config(None)


@pytest.mark.parametrize("test_case", REGEX_CASES, ids=[test_case["id"] for test_case in REGEX_CASES])
def test_targeting_regex_conformance(provider, test_case):
    expected = _rust_rules_based_expectation(test_case)
    assert process_ffe_configuration(_regex_config(test_case["rawPattern"]))

    result = provider.resolve_string_details(
        "regex-conformance",
        "not-matched",
        EvaluationContext(
            targeting_key=test_case["id"],
            attributes={"candidate": test_case["input"]},
        ),
    )

    # Compilation is not exposed by the provider API. A native compile failure
    # is observable only through the evaluator's fail-closed non-match result.
    expected_match = expected["match"] if expected["match"] is not None else False
    assert (result.value == "matched") is expected_match, (
        f"{test_case['id']} expected the Rust rules-based engine to report "
        f"compile={expected['compile']} and match={expected['match']}, got {result}"
    )
