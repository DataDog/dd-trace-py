import mock
import pytest

from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_input_documents
from ddtrace.llmobs.types import Message
from tests.llmobs._utils import assert_llmobs_span_data

from ._utils import get_simple_chat_template


IGNORE_FIELDS = [
    "metrics.vllm.latency.ttft",
    "metrics.vllm.latency.queue",
    "metrics.vllm.latency.prefill",
    "metrics.vllm.latency.decode",
    "metrics.vllm.latency.inference",
]


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_basic(vllm_llmobs, test_spans, opt_125m_llm):
    from vllm import SamplingParams

    llm = opt_125m_llm
    sampling = SamplingParams(temperature=0.1, top_p=0.9, max_tokens=8, seed=42)
    llm.generate("The future of AI is", sampling)
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1

    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="llm",
        model_name="facebook/opt-125m",
        model_provider="vllm",
        input_messages=[Message(content="The future of AI is")],
        output_messages=[Message(role="assistant", content=" in the hands of the people.")],
        metadata={
            "max_tokens": 8,
            "n": 1,
            "temperature": 0.1,
            "top_p": 0.9,
            "finish_reason": "length",
            "num_cached_tokens": 0,
        },
        metrics={
            "input_tokens": 6,
            "output_tokens": 8,
            "total_tokens": 14,
            "time_to_first_token": mock.ANY,
            "time_in_queue": mock.ANY,
            "time_in_model_prefill": mock.ANY,
            "time_in_model_decode": mock.ANY,
            "time_in_model_inference": mock.ANY,
        },
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm", "integration": "vllm"},
    )


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_chat(vllm_llmobs, test_spans, opt_125m_llm):
    from vllm import SamplingParams

    llm = opt_125m_llm
    sampling_params = SamplingParams(seed=42)

    conversation = [
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hello! How can I assist you today?"},
        {"role": "user", "content": "Write an essay about the importance of higher education."},
    ]

    llm.chat(conversation, sampling_params, chat_template=get_simple_chat_template(), use_tqdm=False)
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1

    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="llm",
        model_name="facebook/opt-125m",
        model_provider="vllm",
        input_messages=[
            Message(role="system", content="You are a helpful assistant"),
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hello! How can I assist you today?"),
            Message(role="user", content="Write an essay about the importance of higher education."),
        ],
        output_messages=[Message(role="assistant", content=mock.ANY)],
        metadata={
            "max_tokens": 16,
            "temperature": 1.0,
            "top_p": 1.0,
            "n": 1,
            "finish_reason": "length",
            "num_cached_tokens": mock.ANY,
        },
        metrics={
            "input_tokens": mock.ANY,
            "output_tokens": 16,
            "total_tokens": mock.ANY,
            "time_to_first_token": mock.ANY,
            "time_in_queue": mock.ANY,
            "time_in_model_prefill": mock.ANY,
            "time_in_model_decode": mock.ANY,
            "time_in_model_inference": mock.ANY,
        },
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm", "integration": "vllm"},
    )


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_classify(vllm_llmobs, test_spans, bge_reranker_llm):
    llm = bge_reranker_llm

    prompts = [
        "Hello, my name is",
        "The capital of France is",
    ]

    llm.classify(prompts)
    traces = test_spans.pop_traces()
    spans = [s for t in traces for s in t]

    # Expect one span per input prompt
    assert len(spans) == len(prompts)

    # Spans may be returned in any order; match by input document text.
    spans_by_prompt = {}
    for span in spans:
        documents = get_llmobs_input_documents(span) or []
        assert len(documents) == 1
        spans_by_prompt[documents[0]["text"]] = span

    assert set(spans_by_prompt.keys()) == set(prompts)

    for prompt in prompts:
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans_by_prompt[prompt]),
            span_kind="embedding",
            model_name="BAAI/bge-reranker-v2-m3",
            model_provider="vllm",
            input_documents=[{"text": prompt}],
            output_value="[1 embedding(s) returned with size 1]",
            metadata={"embedding_dim": 1, "num_cached_tokens": 0},
            metrics={
                "input_tokens": 7,
                "output_tokens": 0,
                "total_tokens": 7,
                "time_to_first_token": mock.ANY,
                "time_in_queue": mock.ANY,
                "time_in_model_prefill": mock.ANY,
                "time_in_model_inference": mock.ANY,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm", "integration": "vllm"},
        )


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_embed(vllm_llmobs, test_spans, e5_small_llm):
    llm = e5_small_llm

    prompts = [
        "Hello, my name is",
        "The capital of France is",
    ]

    llm.embed(prompts)
    traces = test_spans.pop_traces()
    spans = [s for t in traces for s in t]

    assert len(spans) == len(prompts)

    spans_by_prompt = {}
    for span in spans:
        documents = get_llmobs_input_documents(span) or []
        assert len(documents) == 1
        spans_by_prompt[documents[0]["text"]] = span

    assert set(spans_by_prompt.keys()) == set(prompts)

    for prompt in prompts:
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans_by_prompt[prompt]),
            span_kind="embedding",
            model_name="intfloat/e5-small",
            model_provider="vllm",
            input_documents=[{"text": prompt}],
            output_value="[1 embedding(s) returned with size 384]",
            metadata={"embedding_dim": 384, "num_cached_tokens": 0},
            metrics={
                "input_tokens": 7,
                "output_tokens": 0,
                "total_tokens": 7,
                "time_to_first_token": mock.ANY,
                "time_in_queue": mock.ANY,
                "time_in_model_prefill": mock.ANY,
                "time_in_model_inference": mock.ANY,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm", "integration": "vllm"},
        )


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_reward(vllm_llmobs, test_spans, bge_reranker_llm):
    llm = bge_reranker_llm

    prompts = [
        "Hello, my name is",
        "The capital of France is",
    ]

    llm.reward(prompts)
    traces = test_spans.pop_traces()
    spans = [s for t in traces for s in t]

    assert len(spans) == len(prompts)

    spans_by_prompt = {}
    for span in spans:
        documents = get_llmobs_input_documents(span) or []
        assert len(documents) == 1
        spans_by_prompt[documents[0]["text"]] = span

    assert set(spans_by_prompt.keys()) == set(prompts)

    for prompt in prompts:
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans_by_prompt[prompt]),
            span_kind="embedding",
            model_name="BAAI/bge-reranker-v2-m3",
            model_provider="vllm",
            input_documents=[{"text": prompt}],
            output_value="[1 embedding(s) returned with size 7]",
            metadata={"embedding_dim": 7, "num_cached_tokens": 0},
            metrics={
                "input_tokens": 7,
                "output_tokens": 0,
                "total_tokens": 7,
                "time_to_first_token": mock.ANY,
                "time_in_queue": mock.ANY,
                "time_in_model_prefill": mock.ANY,
                "time_in_model_inference": mock.ANY,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm", "integration": "vllm"},
        )


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_score(vllm_llmobs, test_spans, bge_reranker_llm):
    llm = bge_reranker_llm

    text_1 = "What is the capital of France?"
    texts_2 = [
        "The capital of Brazil is Brasilia.",
        "The capital of France is Paris.",
    ]

    llm.score(text_1, texts_2)
    traces = test_spans.pop_traces()
    spans = [s for t in traces for s in t]

    # Expect one span per candidate document
    assert len(spans) == len(texts_2)

    expected_token_metrics_by_text = {
        "[0, 4865, 83, 70, 10323, 111, 9942, 32, 2, 2, 581, 10323, 111, 30089, 83, 8233, 399, 5, 2]": {
            "input_tokens": 19,
            "output_tokens": 0,
            "total_tokens": 19,
            "time_to_first_token": mock.ANY,
            "time_in_queue": mock.ANY,
            "time_in_model_prefill": mock.ANY,
            "time_in_model_inference": mock.ANY,
        },
        "[0, 4865, 83, 70, 10323, 111, 9942, 32, 2, 2, 581, 10323, 111, 9942, 83, 7270, 5, 2]": {
            "input_tokens": 18,
            "output_tokens": 0,
            "total_tokens": 18,
            "time_to_first_token": mock.ANY,
            "time_in_queue": mock.ANY,
            "time_in_model_prefill": mock.ANY,
            "time_in_model_inference": mock.ANY,
        },
    }

    for span in spans:
        token_text = get_llmobs_input_documents(span)[0]["text"]
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span),
            span_kind="embedding",
            model_name="BAAI/bge-reranker-v2-m3",
            model_provider="vllm",
            input_documents=[{"text": token_text}],
            output_value="[1 embedding(s) returned with size 1]",
            metadata={"embedding_dim": 1, "num_cached_tokens": 0},
            metrics=expected_token_metrics_by_text[token_text],
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm", "integration": "vllm"},
        )


def test_shadow_tags_completion_when_llmobs_disabled(tracer):
    """Verify shadow tags are set on vLLM spans when LLMObs is disabled."""
    from unittest.mock import MagicMock

    from ddtrace.contrib.internal.vllm.extractors import RequestData
    from ddtrace.llmobs._integrations.vllm import VLLMIntegration

    integration = VLLMIntegration(MagicMock())

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
