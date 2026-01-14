import mock
import pytest

from ddtrace.llmobs.types import Message
from tests.llmobs._utils import _expected_llmobs_llm_span_event

from ._utils import get_simple_chat_template


IGNORE_FIELDS = [
    "metrics.vllm.latency.ttft",
    "metrics.vllm.latency.queue",
    "metrics.vllm.latency.prefill",
    "metrics.vllm.latency.decode",
    "metrics.vllm.latency.inference",
]


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_basic(llmobs_events, test_spans, opt_125m_llm):
    from vllm import SamplingParams

    llm = opt_125m_llm
    sampling = SamplingParams(temperature=0.1, top_p=0.9, max_tokens=8, seed=42)
    llm.generate("The future of AI is", sampling)
    span = test_spans.pop_traces()[0][0]

    assert len(llmobs_events) == 1
    expected = _expected_llmobs_llm_span_event(
        span,
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
        token_metrics={
            "input_tokens": 6,
            "output_tokens": 8,
            "total_tokens": 14,
            "time_to_first_token": mock.ANY,
            "time_in_queue": mock.ANY,
            "time_in_model_prefill": mock.ANY,
            "time_in_model_decode": mock.ANY,
            "time_in_model_inference": mock.ANY,
        },
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"},
    )
    assert llmobs_events[0] == expected


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_chat(llmobs_events, test_spans, opt_125m_llm):
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
    span = test_spans.pop_traces()[0][0]

    assert len(llmobs_events) == 1
    expected = _expected_llmobs_llm_span_event(
        span,
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
        token_metrics={
            "input_tokens": mock.ANY,
            "output_tokens": 16,
            "total_tokens": mock.ANY,
            "time_to_first_token": mock.ANY,
            "time_in_queue": mock.ANY,
            "time_in_model_prefill": mock.ANY,
            "time_in_model_decode": mock.ANY,
            "time_in_model_inference": mock.ANY,
        },
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"},
    )
    assert llmobs_events[0] == expected


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_classify(llmobs_events, test_spans, bge_reranker_llm):
    llm = bge_reranker_llm

    prompts = [
        "Hello, my name is",
        "The capital of France is",
    ]

    llm.classify(prompts)
    traces = test_spans.pop_traces()
    spans = [s for t in traces for s in t]

    # Expect one event per input prompt
    assert len(llmobs_events) == len(prompts) == len(spans)
    span_by_id = {s.span_id: s for s in spans}

    for prompt, event in zip(prompts, llmobs_events):
        span = span_by_id[int(event["span_id"])]
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="embedding",
            model_name="BAAI/bge-reranker-v2-m3",
            model_provider="vllm",
            input_documents=[{"text": prompt}],
            output_value="[1 embedding(s) returned with size 1]",
            metadata={"embedding_dim": 1, "num_cached_tokens": 0},
            token_metrics={
                "input_tokens": 7,
                "output_tokens": 0,
                "total_tokens": 7,
                "time_to_first_token": mock.ANY,
                "time_in_queue": mock.ANY,
                "time_in_model_prefill": mock.ANY,
                "time_in_model_inference": mock.ANY,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"},
        )
        assert event == expected


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_embed(llmobs_events, test_spans, e5_small_llm):
    llm = e5_small_llm

    prompts = [
        "Hello, my name is",
        "The capital of France is",
    ]

    llm.embed(prompts)
    traces = test_spans.pop_traces()
    spans = [s for t in traces for s in t]

    # Expect one event per input prompt
    assert len(llmobs_events) == len(prompts) == len(spans)
    span_by_id = {s.span_id: s for s in spans}

    for prompt, event in zip(prompts, llmobs_events):
        span = span_by_id[int(event["span_id"])]
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="embedding",
            model_name="intfloat/e5-small",
            model_provider="vllm",
            input_documents=[{"text": prompt}],
            output_value="[1 embedding(s) returned with size 384]",
            metadata={"embedding_dim": 384, "num_cached_tokens": 0},
            token_metrics={
                "input_tokens": 7,
                "output_tokens": 0,
                "total_tokens": 7,
                "time_to_first_token": mock.ANY,
                "time_in_queue": mock.ANY,
                "time_in_model_prefill": mock.ANY,
                "time_in_model_inference": mock.ANY,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"},
        )
        assert event == expected


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_reward(llmobs_events, test_spans, bge_reranker_llm):
    llm = bge_reranker_llm

    prompts = [
        "Hello, my name is",
        "The capital of France is",
    ]

    llm.reward(prompts)
    traces = test_spans.pop_traces()
    spans = [s for t in traces for s in t]

    # Expect one event per input prompt
    assert len(llmobs_events) == len(prompts) == len(spans)
    span_by_id = {s.span_id: s for s in spans}

    for prompt, event in zip(prompts, llmobs_events):
        span = span_by_id[int(event["span_id"])]
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="embedding",
            model_name="BAAI/bge-reranker-v2-m3",
            model_provider="vllm",
            input_documents=[{"text": prompt}],
            output_value="[1 embedding(s) returned with size 7]",
            metadata={"embedding_dim": 7, "num_cached_tokens": 0},
            token_metrics={
                "input_tokens": 7,
                "output_tokens": 0,
                "total_tokens": 7,
                "time_to_first_token": mock.ANY,
                "time_in_queue": mock.ANY,
                "time_in_model_prefill": mock.ANY,
                "time_in_model_inference": mock.ANY,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"},
        )
        assert event == expected


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_score(llmobs_events, test_spans, bge_reranker_llm):
    llm = bge_reranker_llm

    text_1 = "What is the capital of France?"
    texts_2 = [
        "The capital of Brazil is Brasilia.",
        "The capital of France is Paris.",
    ]

    llm.score(text_1, texts_2)
    traces = test_spans.pop_traces()
    spans = [s for t in traces for s in t]

    # Expect one event per candidate document
    assert len(llmobs_events) == len(texts_2) == len(spans)
    span_by_id = {s.span_id: s for s in spans}

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

    for event in llmobs_events:
        span = span_by_id[int(event["span_id"])]
        token_text = event["meta"]["input"]["documents"][0]["text"]
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="embedding",
            model_name="BAAI/bge-reranker-v2-m3",
            model_provider="vllm",
            input_documents=[{"text": token_text}],
            output_value="[1 embedding(s) returned with size 1]",
            metadata={"embedding_dim": 1, "num_cached_tokens": 0},
            token_metrics=expected_token_metrics_by_text[token_text],
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"},
        )
        assert event == expected
