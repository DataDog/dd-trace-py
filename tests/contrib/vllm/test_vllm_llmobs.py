import mock
import pytest

from tests.llmobs._utils import _expected_llmobs_llm_span_event

from ._utils import get_simple_chat_template


IGNORE_FIELDS = [
    "metrics.vllm.latency.ttft",
    "metrics.vllm.latency.queue",
    "metrics.vllm.latency.prefill",
    "metrics.vllm.latency.decode",
    "metrics.vllm.latency.inference",
    "metrics.vllm.latency.model_forward",
    "metrics.vllm.latency.model_execute",
]


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_basic(llmobs_events, mock_tracer, vllm_engine_mode, opt_125m_llm):
    from vllm import SamplingParams

    llm = opt_125m_llm
    sampling = SamplingParams(temperature=0.1, top_p=0.9, max_tokens=8, seed=42)
    llm.generate("The future of AI is", sampling)
    span = mock_tracer.pop_traces()[0][0]

    assert len(llmobs_events) == 1
    expected = _expected_llmobs_llm_span_event(
        span,
        model_name="facebook/opt-125m",
        model_provider="vllm",
        input_messages=[{"content": "The future of AI is", "role": ""}],
        output_messages=[{"content": " in the hands of the people.\n", "role": ""}],
        metadata={
            "max_tokens": 8,
            "presence_penalty": 0.0,
            "n": 1,
            "repetition_penalty": 1.0,
            "temperature": 0.1,
            "top_k": 0,
            "frequency_penalty": 0.0,
            "top_p": 0.9,
            "finish_reason": "length",
            "seed": 42,
        }
        | ({"num_cached_tokens": 0} if vllm_engine_mode == "1" else {}),
        token_metrics={"input_tokens": 6, "output_tokens": 8, "total_tokens": 14},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"},
    )
    assert llmobs_events[0] == expected


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_chat(llmobs_events, mock_tracer, vllm_engine_mode, opt_125m_llm):
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
    span = mock_tracer.pop_traces()[0][0]

    assert len(llmobs_events) == 1
    expected = _expected_llmobs_llm_span_event(
        span,
        model_name="facebook/opt-125m",
        model_provider="vllm",
        input_messages=[
            {
                "content": (
                    "You are a helpful assistant\nUser: Hello\nAssistant: Hello! How can I assist you today?\n"
                    "User: Write an essay about the importance of higher education.\nAssistant:"
                ),
                "role": "",
            }
        ],
        output_messages=[
            {
                "content": (
                    " Provide lecture information about INTERESTED universities by translating people's "
                    "ideas into their letters"
                ),
                "role": "",
            }
        ],
        metadata={
            "seed": 42,
            "repetition_penalty": 1.0,
            "max_tokens": 16,
            "top_k": 0,
            "temperature": 1.0,
            "presence_penalty": 0.0,
            "top_p": 1.0,
            "n": 1,
            "frequency_penalty": 0.0,
            "finish_reason": "length",
        }
        | ({"num_cached_tokens": mock.ANY} if vllm_engine_mode == "1" else {}),
        token_metrics={"input_tokens": 37, "output_tokens": 16, "total_tokens": 53},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"},
    )
    assert llmobs_events[0] == expected


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_classify(llmobs_events, mock_tracer, vllm_engine_mode, bge_reranker_llm):
    llm = bge_reranker_llm

    prompts = [
        "Hello, my name is",
        "The capital of France is",
    ]

    llm.classify(prompts)
    traces = mock_tracer.pop_traces()
    spans = [s for t in traces for s in t]

    # Expect one event per input prompt
    assert len(llmobs_events) == len(prompts) == len(spans)
    span_by_id = {s.span_id: s for s in spans}

    expected_token_metrics_by_text = {
        "[0, 35378, 4, 759, 9351, 83, 2]": {"input_tokens": 7, "output_tokens": 0, "total_tokens": 7},
        "[0, 581, 10323, 111, 9942, 83, 2]": {"input_tokens": 7, "output_tokens": 0, "total_tokens": 7},
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
            metadata={"embedding_dim": 1},
            token_metrics=expected_token_metrics_by_text[token_text],
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"},
        )
        assert event == expected


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_embed(llmobs_events, mock_tracer, vllm_engine_mode, e5_small_llm):
    llm = e5_small_llm

    prompts = [
        "Hello, my name is",
        "The capital of France is",
    ]

    llm.embed(prompts)
    traces = mock_tracer.pop_traces()
    spans = [s for t in traces for s in t]

    # Expect one event per input prompt
    assert len(llmobs_events) == len(prompts) == len(spans)
    span_by_id = {s.span_id: s for s in spans}

    expected_token_metrics_by_text = {
        "[101, 7592, 1010, 2026, 2171, 2003, 102]": {"input_tokens": 7, "output_tokens": 0, "total_tokens": 7},
        "[101, 1996, 3007, 1997, 2605, 2003, 102]": {"input_tokens": 7, "output_tokens": 0, "total_tokens": 7},
    }

    for event in llmobs_events:
        span = span_by_id[int(event["span_id"])]
        token_text = event["meta"]["input"]["documents"][0]["text"]
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="embedding",
            model_name="intfloat/e5-small",
            model_provider="vllm",
            input_documents=[{"text": token_text}],
            output_value="[1 embedding(s) returned with size 384]",
            metadata={"embedding_dim": 384},
            token_metrics=expected_token_metrics_by_text[token_text],
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"},
        )
        assert event == expected


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_reward(llmobs_events, mock_tracer, vllm_engine_mode, bge_reranker_llm):
    llm = bge_reranker_llm

    prompts = [
        "Hello, my name is",
        "The capital of France is",
    ]

    llm.reward(prompts)
    traces = mock_tracer.pop_traces()
    spans = [s for t in traces for s in t]

    # Expect one event per input prompt
    assert len(llmobs_events) == len(prompts) == len(spans)
    span_by_id = {s.span_id: s for s in spans}

    expected_token_metrics_by_text = {
        "[0, 35378, 4, 759, 9351, 83, 2]": {"input_tokens": 7, "output_tokens": 0, "total_tokens": 7},
        "[0, 581, 10323, 111, 9942, 83, 2]": {"input_tokens": 7, "output_tokens": 0, "total_tokens": 7},
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
            output_value="[7 embedding(s) returned with size 1024]",
            metadata={"embedding_dim": 1024},
            token_metrics=expected_token_metrics_by_text[token_text],
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"},
        )
        assert event == expected


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_score(llmobs_events, mock_tracer, vllm_engine_mode, bge_reranker_llm):
    llm = bge_reranker_llm

    text_1 = "What is the capital of France?"
    texts_2 = [
        "The capital of Brazil is Brasilia.",
        "The capital of France is Paris.",
    ]

    llm.score(text_1, texts_2)
    traces = mock_tracer.pop_traces()
    spans = [s for t in traces for s in t]

    # Expect one event per candidate document
    assert len(llmobs_events) == len(texts_2) == len(spans)
    span_by_id = {s.span_id: s for s in spans}

    expected_token_metrics_by_text = {
        "[0, 4865, 83, 70, 10323, 111, 9942, 32, 2, 2, 581, 10323, 111, 30089, 83, 8233, 399, 5, 2]": {
            "input_tokens": 19,
            "output_tokens": 0,
            "total_tokens": 19,
        },
        "[0, 4865, 83, 70, 10323, 111, 9942, 32, 2, 2, 581, 10323, 111, 9942, 83, 7270, 5, 2]": {
            "input_tokens": 18,
            "output_tokens": 0,
            "total_tokens": 18,
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
            metadata={"embedding_dim": 1},
            token_metrics=expected_token_metrics_by_text[token_text],
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"},
        )
        assert event == expected
