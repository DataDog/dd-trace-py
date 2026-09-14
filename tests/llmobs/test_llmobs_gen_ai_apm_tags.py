"""Tests for the gen_ai.* attributes emitted onto APM spans."""

import mock
import pytest

from ddtrace.llmobs._constants import GEN_AI_APPLICATION_NAME_TAG_KEY
from ddtrace.llmobs._constants import GEN_AI_CONVERSATION_ID_TAG_KEY
from ddtrace.llmobs._constants import GEN_AI_OPERATION_NAME_TAG_KEY
from ddtrace.llmobs._constants import GEN_AI_PROVIDER_NAME_TAG_KEY
from ddtrace.llmobs._constants import GEN_AI_REQUEST_MODEL_TAG_KEY
from ddtrace.llmobs._constants import GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import GEN_AI_USAGE_CACHE_WRITE_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import GEN_AI_USAGE_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import GEN_AI_USAGE_OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import GEN_AI_USAGE_REASONING_OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import GEN_AI_USAGE_TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import UNKNOWN_MODEL_NAME
from ddtrace.llmobs._constants import UNKNOWN_MODEL_PROVIDER
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import set_gen_ai_apm_tags


ALL_TOKEN_METRICS = {
    "input_tokens": 10,
    "output_tokens": 20,
    "total_tokens": 30,
    "cache_read_input_tokens": 4,
    "cache_write_input_tokens": 5,
    "reasoning_output_tokens": 6,
}


def _usage(span):
    return {
        GEN_AI_USAGE_INPUT_TOKENS_METRIC_KEY: span.get_metric(GEN_AI_USAGE_INPUT_TOKENS_METRIC_KEY),
        GEN_AI_USAGE_OUTPUT_TOKENS_METRIC_KEY: span.get_metric(GEN_AI_USAGE_OUTPUT_TOKENS_METRIC_KEY),
        GEN_AI_USAGE_TOTAL_TOKENS_METRIC_KEY: span.get_metric(GEN_AI_USAGE_TOTAL_TOKENS_METRIC_KEY),
        GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS_METRIC_KEY: span.get_metric(
            GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS_METRIC_KEY
        ),
        GEN_AI_USAGE_CACHE_WRITE_INPUT_TOKENS_METRIC_KEY: span.get_metric(
            GEN_AI_USAGE_CACHE_WRITE_INPUT_TOKENS_METRIC_KEY
        ),
        GEN_AI_USAGE_REASONING_OUTPUT_TOKENS_METRIC_KEY: span.get_metric(
            GEN_AI_USAGE_REASONING_OUTPUT_TOKENS_METRIC_KEY
        ),
    }


def test_llm_span_emits_all_scalars(llmobs, test_spans):
    with llmobs.llm(model_name="gpt-4", model_provider="OpenAI", session_id="sess-1") as span:
        llmobs.annotate(span=span, metrics=ALL_TOKEN_METRICS)
    span = test_spans.pop()[0]

    assert span.get_tag(GEN_AI_OPERATION_NAME_TAG_KEY) == "llm"
    assert span.get_tag(GEN_AI_REQUEST_MODEL_TAG_KEY) == "gpt-4"
    assert span.get_tag(GEN_AI_PROVIDER_NAME_TAG_KEY) == "openai"
    assert span.get_tag(GEN_AI_CONVERSATION_ID_TAG_KEY) == "sess-1"
    assert span.get_tag(GEN_AI_APPLICATION_NAME_TAG_KEY) is not None
    assert _usage(span) == {
        GEN_AI_USAGE_INPUT_TOKENS_METRIC_KEY: 10,
        GEN_AI_USAGE_OUTPUT_TOKENS_METRIC_KEY: 20,
        GEN_AI_USAGE_TOTAL_TOKENS_METRIC_KEY: 30,
        GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS_METRIC_KEY: 4,
        GEN_AI_USAGE_CACHE_WRITE_INPUT_TOKENS_METRIC_KEY: 5,
        GEN_AI_USAGE_REASONING_OUTPUT_TOKENS_METRIC_KEY: 6,
    }


def test_llm_span_without_model_falls_back_to_unknown(llmobs, test_spans):
    with llmobs.llm():
        pass
    span = test_spans.pop()[0]

    assert span.get_tag(GEN_AI_REQUEST_MODEL_TAG_KEY) == UNKNOWN_MODEL_NAME
    assert span.get_tag(GEN_AI_PROVIDER_NAME_TAG_KEY) == UNKNOWN_MODEL_PROVIDER.lower()


def test_agent_span_keeps_model_fields(llmobs, test_spans):
    """_normalize_llmobs_meta pops model_name/model_provider for non-llm kinds, so emission has
    to read them before it runs.
    """
    with llmobs.agent(name="my-agent") as span:
        # Mirrors GoogleAdkIntegration, which stamps model fields on agent spans.
        _annotate_llmobs_span_data(span, model_name="gpt-4o", model_provider="OpenAI")
    span = test_spans.pop()[0]

    assert span.get_tag(GEN_AI_OPERATION_NAME_TAG_KEY) == "agent"
    assert span.get_tag(GEN_AI_REQUEST_MODEL_TAG_KEY) == "gpt-4o"
    assert span.get_tag(GEN_AI_PROVIDER_NAME_TAG_KEY) == "openai"


def test_workflow_span_omits_token_metrics(llmobs, test_spans):
    with llmobs.workflow(name="wf") as span:
        llmobs.annotate(span=span, metrics=ALL_TOKEN_METRICS)
    span = test_spans.pop()[0]

    assert span.get_tag(GEN_AI_OPERATION_NAME_TAG_KEY) == "workflow"
    assert all(value is None for value in _usage(span).values())
    assert span.get_tag(GEN_AI_REQUEST_MODEL_TAG_KEY) is None


def test_tags_survive_user_processor_drop(llmobs, test_spans):
    """A processor dropping the LLMObs event must not strip the still-exported APM span."""
    llmobs.register_processor(lambda _span: None)
    try:
        with llmobs.llm(model_name="gpt-4", model_provider="OpenAI"):
            pass
    finally:
        llmobs.register_processor(None)
    span = test_spans.pop()[0]

    assert span.get_tag(GEN_AI_OPERATION_NAME_TAG_KEY) == "llm"
    assert span.get_tag(GEN_AI_REQUEST_MODEL_TAG_KEY) == "gpt-4"
    assert span.get_tag(GEN_AI_PROVIDER_NAME_TAG_KEY) == "openai"


def test_emission_failure_does_not_break_llmobs_event(llmobs, test_spans, mock_llmobs_logs):
    with mock.patch("ddtrace.llmobs._llmobs.set_gen_ai_apm_tags_from_llmobs_data", side_effect=ValueError("boom")):
        with llmobs.llm(model_name="gpt-4"):
            pass
    span = test_spans.pop()[0]

    assert span.get_tag(GEN_AI_OPERATION_NAME_TAG_KEY) is None
    mock_llmobs_logs.debug.assert_called()


@pytest.mark.parametrize(
    "span_kind,expected_model,expected_provider",
    [
        ("llm", UNKNOWN_MODEL_NAME, UNKNOWN_MODEL_PROVIDER.lower()),
        ("embedding", UNKNOWN_MODEL_NAME, UNKNOWN_MODEL_PROVIDER.lower()),
        ("agent", None, None),
        ("tool", None, None),
    ],
)
def test_set_gen_ai_apm_tags_model_defaults(tracer, span_kind, expected_model, expected_provider):
    """The LLMObs-disabled path goes through this helper directly, with no meta_struct."""
    with tracer.trace("test") as span:
        set_gen_ai_apm_tags(span, span_kind=span_kind, metrics=ALL_TOKEN_METRICS)

        assert span.get_tag(GEN_AI_OPERATION_NAME_TAG_KEY) == span_kind
        assert span.get_tag(GEN_AI_REQUEST_MODEL_TAG_KEY) == expected_model
        assert span.get_tag(GEN_AI_PROVIDER_NAME_TAG_KEY) == expected_provider
        if span_kind in ("llm", "embedding"):
            assert span.get_metric(GEN_AI_USAGE_TOTAL_TOKENS_METRIC_KEY) == 30
        else:
            assert span.get_metric(GEN_AI_USAGE_TOTAL_TOKENS_METRIC_KEY) is None
