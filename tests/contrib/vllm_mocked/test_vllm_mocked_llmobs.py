from types import SimpleNamespace
from unittest import mock

import pytest

from ddtrace.contrib.internal.vllm.extractors import RequestData
from ddtrace.contrib.internal.vllm.extractors import extract_request_data
from ddtrace.llmobs._integrations.vllm import VLLMIntegration
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from tests.llmobs._utils import assert_llmobs_span_data


@pytest.mark.parametrize("embedding", [False, True], ids=["completion", "embedding"])
def test_request_tags_from_engine_output(tracer, embedding):
    state = SimpleNamespace(
        prompt="Hello",
        prompt_token_ids=[1, 2],
        prompt_len=2,
        lora_name=None,
        temperature=None,
        top_p=None,
        n=None,
        max_tokens_param=None,
        detokenizer=SimpleNamespace(output_text="Hi"),
    )
    output = SimpleNamespace(
        finish_reason="stop",
        pooling_output=SimpleNamespace(shape=(1, 4)) if embedding else None,
        prefill_stats=SimpleNamespace(num_cached_tokens=1),
    )
    data = extract_request_data(state, output)
    integration = VLLMIntegration(mock.MagicMock())
    with tracer.trace("vllm.request") as span:
        integration._set_base_span_tags(span, model="test-model")
        integration._llmobs_set_tags(
            span, [], {"request_data": data}, operation="embedding" if embedding else "completion"
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span),
            span_kind="embedding" if embedding else "llm",
            model_name="test-model",
            model_provider="vllm",
            input_documents=[{"text": "Hello"}] if embedding else None,
            output_value="[1 embedding(s) returned with size 4]" if embedding else None,
            input_messages=None if embedding else [{"content": "Hello"}],
            output_messages=None if embedding else [{"role": "assistant", "content": "Hi"}],
            metadata={"num_cached_tokens": 1, "finish_reason": "stop"},
            metrics={"input_tokens": 2, "output_tokens": 0, "total_tokens": 2},
        )


def test_shadow_tags_completion_when_llmobs_disabled(tracer):
    """Verify shadow tags are set on vLLM spans when LLMObs is disabled."""
    integration = VLLMIntegration(mock.MagicMock())

    data = RequestData(input_tokens=20, output_tokens=10)

    with tracer.trace("vllm.request") as span:
        span._set_attribute("vllm.request.model", "meta-llama/Llama-3-8B")
        span._set_attribute("vllm.request.provider", "vllm")
        integration._set_apm_shadow_tags(span, [], {"request_data": data}, response=None, operation="")

    assert span.get_tag("_dd.llmobs.span_kind") == "llm"
    assert span.get_tag("_dd.llmobs.model_name") == "meta-llama/Llama-3-8B"
    assert span.get_tag("_dd.llmobs.model_provider") == "vllm"
    assert span.get_metric("_dd.llmobs.enabled") == 0
    assert span.get_metric("_dd.llmobs.input_tokens") == 20
    assert span.get_metric("_dd.llmobs.output_tokens") == 10
    assert span.get_metric("_dd.llmobs.total_tokens") == 30
