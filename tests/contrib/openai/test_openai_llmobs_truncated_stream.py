import mock

from ddtrace.llmobs._constants import CACHED_LLMOBS_EVENT_CTX_KEY
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.openai import OpenAIIntegration
from ddtrace.llmobs._integrations.utils import _est_tokens
from ddtrace.llmobs._utils import get_llmobs_span_kind


class _TupleIteratingResponse:
    """Minimal Pydantic-like Responses object whose iteration yields field/value tuples."""

    usage = None

    def __init__(self):
        self.output = [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "partial response"}],
            }
        ]

    def __iter__(self):
        return iter((("id", "resp_test"), ("output", self.output)))


def test_truncated_response_stream_metrics_do_not_iterate_response_model():
    response = _TupleIteratingResponse()

    metrics = OpenAIIntegration._extract_llmobs_metrics_tags(
        mock.MagicMock(),
        response,
        "llm",
        {"stream": True},
    )

    output_tokens = _est_tokens("partial response")
    assert metrics == {
        INPUT_TOKENS_METRIC_KEY: 0,
        OUTPUT_TOKENS_METRIC_KEY: output_tokens,
        TOTAL_TOKENS_METRIC_KEY: output_tokens,
    }


def test_response_span_kept_when_metrics_extraction_raises(openai, openai_llmobs):
    integration = openai._datadog_integration
    span = integration.trace("createResponse", submit_to_llmobs=True)

    with mock.patch.object(
        integration,
        "_extract_llmobs_metrics_tags",
        side_effect=ValueError("malformed streamed response"),
    ):
        integration.llmobs_set_tags(
            span,
            args=[],
            kwargs={"input": "hello", "stream": True},
            response=None,
            operation="response",
        )

    assert get_llmobs_span_kind(span) == "llm"

    span.finish()
    assert span._get_ctx_item(CACHED_LLMOBS_EVENT_CTX_KEY) is not None
