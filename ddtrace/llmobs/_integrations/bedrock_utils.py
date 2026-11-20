from ddtrace.llmobs._constants import CACHE_READ_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import CACHE_WRITE_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY


def normalize_input_tokens(usage_metrics: dict) -> None:
    """
    `input_tokens` in bedrock's response usage metadata is the number of non-cached tokens. We normalize it to mean
    the total tokens sent to the model to be consistent with other model providers.

    Args:
        usage_metrics: Dictionary containing token usage metrics that will be modified in-place
    """
    if CACHE_READ_INPUT_TOKENS_METRIC_KEY in usage_metrics or CACHE_WRITE_INPUT_TOKENS_METRIC_KEY in usage_metrics:
        input_tokens = usage_metrics.get(INPUT_TOKENS_METRIC_KEY, 0)
        cache_read_tokens = usage_metrics.get(CACHE_READ_INPUT_TOKENS_METRIC_KEY, 0)
        cache_write_tokens = usage_metrics.get(CACHE_WRITE_INPUT_TOKENS_METRIC_KEY, 0)
        usage_metrics[INPUT_TOKENS_METRIC_KEY] = input_tokens + cache_read_tokens + cache_write_tokens
