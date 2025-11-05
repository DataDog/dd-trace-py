from types import SimpleNamespace

from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.google_utils import extract_generation_metrics_google_genai


def test_extract_generation_metrics_google_genai_handles_missing_token_counts():
    response = SimpleNamespace(
        usage_metadata={
            "prompt_token_count": None,
            "candidates_token_count": None,
            "thoughts_token_count": None,
            "total_token_count": None,
        }
    )

    metrics = extract_generation_metrics_google_genai(response)

    assert metrics == {}


def test_extract_generation_metrics_google_genai_falls_back_to_sum_when_available():
    response = SimpleNamespace(
        usage_metadata={
            "prompt_token_count": 6,
            "candidates_token_count": 4,
            "thoughts_token_count": None,
            "total_token_count": None,
        }
    )

    metrics = extract_generation_metrics_google_genai(response)

    assert metrics == {
        INPUT_TOKENS_METRIC_KEY: 6,
        OUTPUT_TOKENS_METRIC_KEY: 4,
        TOTAL_TOKENS_METRIC_KEY: 10,
    }
